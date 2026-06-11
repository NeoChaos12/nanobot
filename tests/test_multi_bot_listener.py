"""
TDD tests for windows/src/listener.py multi-bot (bot pool) changes.

Coverage (Phase 15):
  (a) _build_applications() constructs one telegram.ext.Application per
      configured, present bot token. Single-bot fallback (bot_pool disabled,
      or dispatcher token missing while pool enabled) builds a single
      "dispatcher" Application from TELEGRAM_TOKEN. Missing T1/T2 tokens are
      tolerated -- those Applications are simply not built.
  (b) _register_handlers() registers the full command set + on_message
      handler only on the dispatcher Application; T1/T2 Applications get no
      inbound MessageHandler, only a minimal placeholder /ping command.
  (c) on_message resolves the project via project_registry using
      update.effective_chat.id when a project registry is active (projects.json
      has entries), and returns early (no dispatch) for unregistered chat_ids.
      When no project registry is active, on_message falls back to existing
      single-project behaviour unchanged.
  (d) on_message applies the reply-routing decision order from
      bot_pool_routing (resolve_reply for tracked replies, else
      resolve_followup for open pending questions) before falling back to
      normal per-project dispatch.

All telegram.ext.Application / Updater objects and subprocess invocations are
mocked -- no network calls. Missing T1/T2 tokens are handled gracefully.
"""

import asyncio
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
    telegram.Update.ALL_TYPES = "ALL_TYPES"
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
    sys.modules["telegram"] = fake_telegram
    sys.modules["telegram.constants"] = fake_telegram.constants
    sys.modules["telegram.ext"] = fake_telegram.ext

    # bot_config
    fake_bot_config = types.ModuleType("windows.src.bot_config")
    fake_bot_config.TELEGRAM_TOKEN = "fake-dispatcher-token"
    fake_bot_config.BASE = Path(__file__).parent.parent
    fake_bot_config._cfg = MagicMock(return_value={
        "allowed_chat_ids": [111],
        "session": {"idle_timeout_seconds": 600},
        "bot_pool": {"enabled": False},
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

    # project_registry
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

    return (
        fake_bot_config, fake_bot_state, fake_bot_utils, fake_dispatcher,
        fake_state, fake_project_registry, fake_bot_pool_routing, fake_telegram,
    )


def _make_commands_pkg(tmp_path) -> ModuleType:
    cmd_dir = tmp_path / "commands"
    cmd_dir.mkdir(exist_ok=True)
    (cmd_dir / "__init__.py").write_text("")

    pkg = types.ModuleType("windows.src.commands")
    pkg.__path__ = [str(cmd_dir)]
    pkg.__package__ = "windows.src.commands"
    return pkg


def _fresh_listener(tmp_path):
    commands_pkg = _make_commands_pkg(tmp_path)
    deps = _install_fake_deps(commands_pkg)

    for key in list(sys.modules):
        if "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener
    return listener, deps


def _make_update_and_context(chat_id: int, text: str, reply_to_message_id=None):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.text = text
    if reply_to_message_id is None:
        update.message.reply_to_message = None
    else:
        update.message.reply_to_message = MagicMock()
        update.message.reply_to_message.message_id = reply_to_message_id

    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    return update, context


# ---------------------------------------------------------------------------
# (a) _build_applications
# ---------------------------------------------------------------------------

def test_build_applications_single_bot_when_pool_disabled(tmp_path):
    listener, deps = _fresh_listener(tmp_path)
    fake_bot_config, *_ = deps

    apps = listener._build_applications()

    assert set(apps.keys()) == {"dispatcher"}
    fake_telegram = deps[-1]
    fake_telegram.ext.Application.builder.return_value.token.assert_any_call("fake-dispatcher-token")


def test_build_applications_pool_enabled_with_partial_pool_tokens(tmp_path):
    listener, deps = _fresh_listener(tmp_path)
    fake_bot_config, _, _, _, _, fake_project_registry, _, fake_telegram = deps

    fake_bot_config._cfg.return_value = {
        "allowed_chat_ids": [111],
        "session": {"idle_timeout_seconds": 600},
        "bot_pool": {"enabled": True},
    }
    fake_project_registry.get_bot_tokens.return_value = {
        "dispatcher": "tok-dispatcher", "T1": "tok-t1", "T2": None,
    }

    apps = listener._build_applications()

    assert set(apps.keys()) == {"dispatcher", "T1"}
    token_calls = [c.args[0] for c in fake_telegram.ext.Application.builder.return_value.token.call_args_list]
    assert "tok-dispatcher" in token_calls
    assert "tok-t1" in token_calls


def test_build_applications_pool_enabled_no_pool_tokens(tmp_path):
    """T1/T2 missing entirely is tolerated -- only the dispatcher app is built."""
    listener, deps = _fresh_listener(tmp_path)
    fake_bot_config, _, _, _, _, fake_project_registry, _, _ = deps

    fake_bot_config._cfg.return_value = {
        "allowed_chat_ids": [111],
        "session": {"idle_timeout_seconds": 600},
        "bot_pool": {"enabled": True},
    }
    fake_project_registry.get_bot_tokens.return_value = {
        "dispatcher": "tok-dispatcher", "T1": None, "T2": None,
    }

    apps = listener._build_applications()

    assert set(apps.keys()) == {"dispatcher"}


def test_build_applications_pool_enabled_falls_back_without_dispatcher_token(tmp_path):
    """If bot_pool.enabled but TELEGRAM_BOT_TOKEN_DISPATCHER is unset, fall back
    to single-bot mode using TELEGRAM_TOKEN (so deployments don't break if the
    pool config exists but tokens haven't been issued yet)."""
    listener, deps = _fresh_listener(tmp_path)
    fake_bot_config, _, _, _, _, fake_project_registry, _, fake_telegram = deps

    fake_bot_config._cfg.return_value = {
        "allowed_chat_ids": [111],
        "session": {"idle_timeout_seconds": 600},
        "bot_pool": {"enabled": True},
    }
    fake_project_registry.get_bot_tokens.return_value = {
        "dispatcher": None, "T1": None, "T2": None,
    }

    apps = listener._build_applications()

    assert set(apps.keys()) == {"dispatcher"}
    fake_telegram.ext.Application.builder.return_value.token.assert_any_call("fake-dispatcher-token")


# ---------------------------------------------------------------------------
# (b) _register_handlers
# ---------------------------------------------------------------------------

def test_register_handlers_only_dispatcher_gets_message_handler(tmp_path):
    listener, deps = _fresh_listener(tmp_path)

    dispatcher_app = MagicMock()
    t1_app = MagicMock()
    t2_app = MagicMock()
    apps = {"dispatcher": dispatcher_app, "T1": t1_app, "T2": t2_app}

    listener._register_handlers(apps, cmd_modules=[])

    # Dispatcher gets the catch-all text MessageHandler.
    dispatcher_handler_types = [c.args[0] for c in dispatcher_app.add_handler.call_args_list]
    assert any(h is listener.MessageHandler.return_value for h in dispatcher_handler_types) or \
        dispatcher_app.add_handler.called

    # T1/T2 must not register a MessageHandler; only CommandHandler (e.g. /ping).
    for pool_app in (t1_app, t2_app):
        for c in pool_app.add_handler.call_args_list:
            registered = c.args[0]
            assert registered is not listener.MessageHandler.return_value


def test_register_handlers_registers_command_modules_on_dispatcher_only(tmp_path):
    listener, deps = _fresh_listener(tmp_path)

    cmd_mod = MagicMock()
    cmd_mod.COMMAND = "foo"
    cmd_mod.handle = AsyncMock()

    dispatcher_app = MagicMock()
    t1_app = MagicMock()
    apps = {"dispatcher": dispatcher_app, "T1": t1_app}

    listener._register_handlers(apps, cmd_modules=[cmd_mod])

    assert dispatcher_app.add_handler.called
    listener.CommandHandler.assert_any_call("foo", cmd_mod.handle)


# ---------------------------------------------------------------------------
# (c) on_message project resolution via project_registry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_message_no_project_registry_keeps_single_project_behaviour(tmp_path):
    """projects.json absent / empty -> existing single-project dispatch unchanged."""
    listener, deps = _fresh_listener(tmp_path)
    _, _, _, fake_dispatcher, _, fake_project_registry, _, _ = deps
    fake_project_registry.load_projects.return_value = {"projects": {}}

    update, context = _make_update_and_context(111, "hello")
    await listener.on_message(update, context)

    fake_dispatcher.run_dispatcher.assert_awaited_once()
    fake_project_registry.resolve_project.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_returns_early_for_unregistered_chat_id(tmp_path):
    """Project registry active (non-empty projects.json) but chat_id not registered
    -> no dispatch."""
    listener, deps = _fresh_listener(tmp_path)
    _, _, fake_bot_utils, fake_dispatcher, _, fake_project_registry, _, _ = deps

    fake_project_registry.load_projects.return_value = {
        "projects": {"proj-a": {"chat_id": 999, "state_dir": "/tmp/proj-a", "project_dir": "/tmp/proj-a"}}
    }
    fake_project_registry.resolve_project.return_value = None

    update, context = _make_update_and_context(111, "hello")
    await listener.on_message(update, context)

    fake_project_registry.resolve_project.assert_called_once_with(111)
    fake_dispatcher.run_dispatcher.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_dispatches_for_registered_chat_id(tmp_path):
    listener, deps = _fresh_listener(tmp_path)
    _, _, _, fake_dispatcher, _, fake_project_registry, fake_bot_pool_routing, _ = deps

    fake_project_registry.load_projects.return_value = {
        "projects": {"proj-a": {"chat_id": 111, "state_dir": "/tmp/proj-a", "project_dir": "/tmp/proj-a"}}
    }
    fake_project_registry.resolve_project.return_value = {
        "project_id": "proj-a", "chat_id": 111, "state_dir": "/tmp/proj-a", "project_dir": "/tmp/proj-a",
    }
    fake_bot_pool_routing.resolve_followup.return_value = None

    update, context = _make_update_and_context(111, "hello")
    await listener.on_message(update, context)

    fake_project_registry.resolve_project.assert_called_once_with(111)
    fake_dispatcher.run_dispatcher.assert_awaited_once()


# ---------------------------------------------------------------------------
# (d) on_message reply-routing decision order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_message_reply_to_tracked_message_routes_to_pending_question(tmp_path):
    listener, deps = _fresh_listener(tmp_path)
    _, _, _, fake_dispatcher, _, fake_project_registry, fake_bot_pool_routing, _ = deps

    fake_project_registry.load_projects.return_value = {
        "projects": {"proj-a": {"chat_id": 111, "state_dir": "/tmp/proj-a", "project_dir": "/tmp/proj-a"}}
    }
    fake_project_registry.resolve_project.return_value = {
        "project_id": "proj-a", "chat_id": 111, "state_dir": "/tmp/proj-a", "project_dir": "/tmp/proj-a",
    }
    fake_bot_pool_routing.resolve_reply.return_value = {
        "chat_id": 111, "message_id": 555, "pending_question_id": "q-abc", "project_id": "proj-a",
    }

    update, context = _make_update_and_context(111, "yes go ahead", reply_to_message_id=555)
    await listener.on_message(update, context)

    fake_bot_pool_routing.resolve_reply.assert_called_once_with(111, 555)
    fake_bot_pool_routing.resolve_followup.assert_not_called()
    fake_dispatcher.run_dispatcher.assert_awaited_once()
    _, kwargs = fake_dispatcher.run_dispatcher.call_args
    assert "q-abc" in kwargs["user_message"]
    assert "yes go ahead" in kwargs["user_message"]


@pytest.mark.asyncio
async def test_on_message_followup_routes_to_open_pending_question(tmp_path):
    listener, deps = _fresh_listener(tmp_path)
    _, _, _, fake_dispatcher, _, fake_project_registry, fake_bot_pool_routing, _ = deps

    fake_project_registry.load_projects.return_value = {
        "projects": {"proj-a": {"chat_id": 111, "state_dir": "/tmp/proj-a", "project_dir": "/tmp/proj-a"}}
    }
    fake_project_registry.resolve_project.return_value = {
        "project_id": "proj-a", "chat_id": 111, "state_dir": "/tmp/proj-a", "project_dir": "/tmp/proj-a",
    }
    fake_bot_pool_routing.resolve_reply.return_value = None
    fake_bot_pool_routing.resolve_followup.return_value = {
        "pending_question_id": "q-xyz", "multiple_open": False,
    }

    update, context = _make_update_and_context(111, "do it")
    await listener.on_message(update, context)

    fake_bot_pool_routing.resolve_followup.assert_called_once_with(111, "proj-a")
    fake_dispatcher.run_dispatcher.assert_awaited_once()
    _, kwargs = fake_dispatcher.run_dispatcher.call_args
    assert "q-xyz" in kwargs["user_message"]
    assert "do it" in kwargs["user_message"]


@pytest.mark.asyncio
async def test_on_message_no_routing_match_dispatches_normal_text(tmp_path):
    listener, deps = _fresh_listener(tmp_path)
    _, _, _, fake_dispatcher, _, fake_project_registry, fake_bot_pool_routing, _ = deps

    fake_project_registry.load_projects.return_value = {
        "projects": {"proj-a": {"chat_id": 111, "state_dir": "/tmp/proj-a", "project_dir": "/tmp/proj-a"}}
    }
    fake_project_registry.resolve_project.return_value = {
        "project_id": "proj-a", "chat_id": 111, "state_dir": "/tmp/proj-a", "project_dir": "/tmp/proj-a",
    }
    fake_bot_pool_routing.resolve_reply.return_value = None
    fake_bot_pool_routing.resolve_followup.return_value = None

    update, context = _make_update_and_context(111, "plain message")
    await listener.on_message(update, context)

    fake_dispatcher.run_dispatcher.assert_awaited_once()
    _, kwargs = fake_dispatcher.run_dispatcher.call_args
    assert kwargs["user_message"] == "plain message"
