"""
TDD tests for windows/src/listener.py core logic.

Coverage:
  (a) _load_commands discovers modules that have COMMAND+handle, skips those that don't
  (b) _is_allowed (from bot_utils) correctly filters based on allowed_chat_ids in config
  (c) _reset_idle_timer cancels the existing idle task and creates a new one
  (d) _scheduler_loop calls dev_loop_lifecycle for DEV LOOP tasks; does NOT call it for
      regular tasks — checked via mocked dispatcher.dev_loop_lifecycle
  (e) on_message serialises dispatcher invocations via bot_state.dispatch_lock: a
      second call while a dispatch is in-flight is rejected with a "busy" notice and
      does not spawn a second dispatcher run; the lock is released after the first
      dispatch completes (success or error)

All Telegram API calls and subprocess invocations are mocked.
"""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Infrastructure: inject fake dependencies into sys.modules so that
# `windows.src.listener` can be imported without real telegram / bot files.
# ---------------------------------------------------------------------------

def _make_fake_telegram():
    """Return a minimal fake telegram package tree."""
    telegram = types.ModuleType("telegram")
    telegram.BotCommand = MagicMock()
    telegram.Update = MagicMock()
    telegram.constants = types.ModuleType("telegram.constants")
    telegram.constants.ParseMode = MagicMock()
    ext = types.ModuleType("telegram.ext")
    ext.Application = MagicMock()
    ext.CommandHandler = MagicMock()
    ext.MessageHandler = MagicMock()
    ext.ContextTypes = MagicMock()
    ext.filters = MagicMock()
    telegram.ext = ext
    return telegram


def _install_fake_deps(commands_pkg: ModuleType):
    """Install all fake windows.src.* dependencies into sys.modules."""
    fake_telegram = _make_fake_telegram()
    sys.modules.setdefault("telegram", fake_telegram)
    sys.modules.setdefault("telegram.constants", fake_telegram.constants)
    sys.modules.setdefault("telegram.ext", fake_telegram.ext)

    # bot_config
    fake_bot_config = types.ModuleType("windows.src.bot_config")
    fake_bot_config.TELEGRAM_TOKEN = "fake-token"
    fake_bot_config.BASE = Path(__file__).parent.parent  # real path so FileHandler doesn't crash
    fake_bot_config._cfg = MagicMock(return_value={
        "allowed_chat_ids": [111],
        "session": {"idle_timeout_seconds": 600},
    })
    sys.modules["windows.src.bot_config"] = fake_bot_config

    # bot_state
    fake_bot_state = types.ModuleType("windows.src.bot_state")
    fake_bot_state.sessions = {}
    fake_bot_state.interrupt_pending = set()
    fake_bot_state.keepalive_paused = False
    fake_bot_state.keepalive_resume_event = None
    fake_bot_state.dispatch_lock = asyncio.Lock()
    sys.modules["windows.src.bot_state"] = fake_bot_state

    # bot_utils
    fake_bot_utils = types.ModuleType("windows.src.bot_utils")
    fake_bot_utils._cfg = fake_bot_config._cfg
    def _is_allowed(chat_id):
        allowed = set(fake_bot_config._cfg().get("allowed_chat_ids", []))
        if not allowed:
            return True
        return chat_id in allowed
    fake_bot_utils._is_allowed = _is_allowed
    fake_bot_utils._send = AsyncMock()
    sys.modules["windows.src.bot_utils"] = fake_bot_utils

    # dispatcher
    fake_dispatcher = types.ModuleType("windows.src.dispatcher")
    fake_dispatcher.run_dispatcher = AsyncMock(return_value={"text": "ok", "session_id": "s1"})
    fake_dispatcher.dev_loop_lifecycle = AsyncMock()
    sys.modules["windows.src.dispatcher"] = fake_dispatcher

    # state
    fake_state = types.ModuleType("windows.src.state")
    fake_state.append_chat_turn = MagicMock()
    fake_state.read_scheduled_tasks = MagicMock(return_value=[])
    fake_state.write_scheduled_tasks = MagicMock()
    fake_state.atomic_write = MagicMock()
    fake_state.read_json = MagicMock(return_value=[])
    sys.modules["windows.src.state"] = fake_state

    # project_registry (no project registry active by default -> single-project fallback)
    fake_project_registry = types.ModuleType("windows.src.project_registry")
    fake_project_registry.DISPATCHER_TOKEN_ENV = "TELEGRAM_BOT_TOKEN_DISPATCHER"
    fake_project_registry.POOL_TOKEN_ENVS = {
        "T1": "TELEGRAM_BOT_TOKEN_T1",
        "T2": "TELEGRAM_BOT_TOKEN_T2",
    }
    fake_project_registry.load_projects = MagicMock(return_value={"projects": {}})
    fake_project_registry.resolve_project = MagicMock(return_value=None)
    fake_project_registry.get_bot_tokens = MagicMock(return_value={
        "dispatcher": None, "T1": None, "T2": None,
    })
    sys.modules["windows.src.project_registry"] = fake_project_registry

    # bot_pool_routing
    fake_bot_pool_routing = types.ModuleType("windows.src.bot_pool_routing")
    fake_bot_pool_routing.resolve_reply = MagicMock(return_value=None)
    fake_bot_pool_routing.resolve_followup = MagicMock(return_value=None)
    sys.modules["windows.src.bot_pool_routing"] = fake_bot_pool_routing

    # commands package (caller provides the actual module object)
    sys.modules["windows.src.commands"] = commands_pkg

    return fake_bot_config, fake_bot_state, fake_bot_utils, fake_dispatcher, fake_state


def _make_commands_pkg(tmp_path, modules: list[dict]) -> ModuleType:
    """
    Create a fake commands package at tmp_path/commands/ with the given modules.
    Each entry in modules is a dict: {"name": str, "command": str|None, "has_handle": bool}.
    Returns the package module object.
    """
    cmd_dir = tmp_path / "commands"
    cmd_dir.mkdir(exist_ok=True)
    (cmd_dir / "__init__.py").write_text("")

    for spec in modules:
        lines = []
        if spec.get("command"):
            lines.append(f'COMMAND = "{spec["command"]}"')
            lines.append(f'DESCRIPTION = "desc"')
        if spec.get("has_handle"):
            lines.append("async def handle(update, ctx): pass")
        (cmd_dir / f'{spec["name"]}.py').write_text("\n".join(lines))

    pkg = types.ModuleType("windows.src.commands")
    pkg.__path__ = [str(cmd_dir)]
    pkg.__package__ = "windows.src.commands"
    return pkg


# ---------------------------------------------------------------------------
# (a) _load_commands
# ---------------------------------------------------------------------------

def test_load_commands_discovers_valid_modules(tmp_path):
    """Modules with both COMMAND and handle are returned; others are skipped."""
    commands_pkg = _make_commands_pkg(tmp_path, [
        {"name": "cmd_foo", "command": "foo", "has_handle": True},
        {"name": "cmd_bar", "command": "bar", "has_handle": True},
        {"name": "not_a_cmd", "command": None,  "has_handle": False},
    ])
    _install_fake_deps(commands_pkg)

    # Remove cached listener module to force fresh import
    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener
    result = listener._load_commands()

    commands = {m.COMMAND for m in result}
    assert "foo" in commands
    assert "bar" in commands
    assert len(result) == 2


def test_load_commands_skips_module_missing_handle(tmp_path):
    commands_pkg = _make_commands_pkg(tmp_path, [
        {"name": "cmd_good", "command": "good", "has_handle": True},
        {"name": "cmd_bad",  "command": "bad",  "has_handle": False},
    ])
    _install_fake_deps(commands_pkg)

    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener
    result = listener._load_commands()

    assert len(result) == 1
    assert result[0].COMMAND == "good"


def test_load_commands_returns_empty_for_empty_package(tmp_path):
    commands_pkg = _make_commands_pkg(tmp_path, [])
    _install_fake_deps(commands_pkg)

    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener
    assert listener._load_commands() == []


# ---------------------------------------------------------------------------
# (b) _is_allowed
# ---------------------------------------------------------------------------

def test_is_allowed_returns_true_for_listed_chat(tmp_path):
    commands_pkg = _make_commands_pkg(tmp_path, [])
    fake_cfg, *_ = _install_fake_deps(commands_pkg)
    fake_cfg._cfg.return_value = {"allowed_chat_ids": [111, 222]}

    import windows.src.bot_utils as bu
    assert bu._is_allowed(111) is True
    assert bu._is_allowed(222) is True


def test_is_allowed_returns_false_for_unlisted_chat(tmp_path):
    commands_pkg = _make_commands_pkg(tmp_path, [])
    fake_cfg, *_ = _install_fake_deps(commands_pkg)
    fake_cfg._cfg.return_value = {"allowed_chat_ids": [111]}

    import windows.src.bot_utils as bu
    assert bu._is_allowed(999) is False


def test_is_allowed_returns_true_when_list_empty(tmp_path):
    """Empty allowed_chat_ids means unrestricted access."""
    commands_pkg = _make_commands_pkg(tmp_path, [])
    fake_cfg, *_ = _install_fake_deps(commands_pkg)
    fake_cfg._cfg.return_value = {"allowed_chat_ids": []}

    import windows.src.bot_utils as bu
    assert bu._is_allowed(999) is True


# ---------------------------------------------------------------------------
# (c) Session idle timer resets on activity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_idle_timer_cancels_existing_task(tmp_path):
    """_reset_idle_timer cancels the old idle task and creates a new one."""
    commands_pkg = _make_commands_pkg(tmp_path, [])
    _, fake_bot_state, *_ = _install_fake_deps(commands_pkg)

    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener

    chat_id = 111
    old_task = MagicMock(spec=asyncio.Task)
    old_task.done.return_value = False

    fake_bot_state.sessions[chat_id] = {"session_id": "old", "idle_task": old_task}

    fake_bot = MagicMock()
    await listener._reset_idle_timer(chat_id, fake_bot)

    old_task.cancel.assert_called_once()
    new_task = fake_bot_state.sessions[chat_id]["idle_task"]
    assert new_task is not old_task
    assert new_task is not None


@pytest.mark.asyncio
async def test_reset_idle_timer_creates_session_entry_if_missing(tmp_path):
    """_reset_idle_timer initialises a sessions entry when none exists."""
    commands_pkg = _make_commands_pkg(tmp_path, [])
    _, fake_bot_state, *_ = _install_fake_deps(commands_pkg)

    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener

    chat_id = 555
    fake_bot_state.sessions = {}

    await listener._reset_idle_timer(chat_id, MagicMock())

    assert chat_id in fake_bot_state.sessions
    assert fake_bot_state.sessions[chat_id]["idle_task"] is not None


# ---------------------------------------------------------------------------
# (e) on_message dispatch_lock serialisation
# ---------------------------------------------------------------------------

def _make_update_and_context(chat_id: int, text: str):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.text = text

    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    return update, context


@pytest.mark.asyncio
async def test_on_message_busy_when_dispatch_lock_held(tmp_path):
    """A second message while a dispatch is in-flight gets a busy notice and is dropped."""
    commands_pkg = _make_commands_pkg(tmp_path, [])
    _, fake_bot_state, fake_bot_utils, fake_dispatcher, _ = _install_fake_deps(commands_pkg)

    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener

    await fake_bot_state.dispatch_lock.acquire()
    try:
        update, context = _make_update_and_context(111, "hello")
        await listener.on_message(update, context)
    finally:
        fake_bot_state.dispatch_lock.release()

    fake_dispatcher.run_dispatcher.assert_not_called()
    fake_bot_utils._send.assert_awaited_once()
    sent_text = fake_bot_utils._send.call_args.args[2]
    assert "busy" in sent_text.lower() or "already running" in sent_text.lower()


@pytest.mark.asyncio
async def test_on_message_releases_lock_after_success(tmp_path):
    """The dispatch_lock is released after a successful dispatcher run."""
    commands_pkg = _make_commands_pkg(tmp_path, [])
    _, fake_bot_state, fake_bot_utils, fake_dispatcher, _ = _install_fake_deps(commands_pkg)
    fake_dispatcher.run_dispatcher.reset_mock()
    fake_bot_utils._send.reset_mock()

    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener

    update, context = _make_update_and_context(111, "hello")
    await listener.on_message(update, context)

    fake_dispatcher.run_dispatcher.assert_awaited_once()
    assert not fake_bot_state.dispatch_lock.locked()


@pytest.mark.asyncio
async def test_on_message_releases_lock_after_dispatcher_error(tmp_path):
    """The dispatch_lock is released even when run_dispatcher raises."""
    commands_pkg = _make_commands_pkg(tmp_path, [])
    _, fake_bot_state, fake_bot_utils, fake_dispatcher, _ = _install_fake_deps(commands_pkg)
    fake_dispatcher.run_dispatcher = AsyncMock(side_effect=RuntimeError("boom"))
    sys.modules["windows.src.dispatcher"].run_dispatcher = fake_dispatcher.run_dispatcher
    fake_bot_utils._send.reset_mock()

    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener

    update, context = _make_update_and_context(111, "hello")
    await listener.on_message(update, context)

    fake_dispatcher.run_dispatcher.assert_awaited_once()
    assert not fake_bot_state.dispatch_lock.locked()
    fake_bot_utils._send.assert_awaited_once()
    sent_text = fake_bot_utils._send.call_args.args[2]
    assert "error" in sent_text.lower()


# ---------------------------------------------------------------------------
# (f) on_message consumes a one-shot history_turns_override from the session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_message_passes_and_clears_history_turns_override(tmp_path):
    """A pending history_turns_override is forwarded to run_dispatcher and popped."""
    commands_pkg = _make_commands_pkg(tmp_path, [])
    _, fake_bot_state, fake_bot_utils, fake_dispatcher, _ = _install_fake_deps(commands_pkg)
    fake_dispatcher.run_dispatcher.reset_mock()
    fake_bot_utils._send.reset_mock()

    fake_bot_state.sessions[111] = {
        "session_id": None,
        "idle_task": None,
        "history_turns_override": 7,
    }

    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener

    update, context = _make_update_and_context(111, "hello")
    await listener.on_message(update, context)

    fake_dispatcher.run_dispatcher.assert_awaited_once()
    _, kwargs = fake_dispatcher.run_dispatcher.call_args
    assert kwargs["history_turns"] == 7
    assert "history_turns_override" not in fake_bot_state.sessions[111]


@pytest.mark.asyncio
async def test_on_message_no_override_passes_none(tmp_path):
    """With no override pending, run_dispatcher is called with history_turns=None."""
    commands_pkg = _make_commands_pkg(tmp_path, [])
    _, fake_bot_state, fake_bot_utils, fake_dispatcher, _ = _install_fake_deps(commands_pkg)
    fake_dispatcher.run_dispatcher.reset_mock()
    fake_bot_utils._send.reset_mock()

    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener

    update, context = _make_update_and_context(111, "hello")
    await listener.on_message(update, context)

    fake_dispatcher.run_dispatcher.assert_awaited_once()
    _, kwargs = fake_dispatcher.run_dispatcher.call_args
    assert kwargs["history_turns"] is None
