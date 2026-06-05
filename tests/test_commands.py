"""
TDD tests for windows/src/commands/ package.

Coverage:
  (a) Each cmd_*.py module exports COMMAND (str), DESCRIPTION (str), and handle (coroutine)
  (b) cmd_help.handle sends HTML text that contains each registered command name
  (c) cmd_end.handle closes the active session (removes it from bot_state.sessions)
  (d) cmd_tasks.handle with no args returns a task list or 'no tasks' message

All Telegram API calls and subprocess invocations are mocked.
"""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Infrastructure: inject fake dependencies so commands can be imported
# without real telegram / bot files present.
# ---------------------------------------------------------------------------

ALLOWED_CHAT_ID = 111


def _make_fake_telegram():
    telegram = types.ModuleType("telegram")
    telegram.Update = MagicMock()
    telegram.constants = types.ModuleType("telegram.constants")
    telegram.constants.ParseMode = MagicMock()
    ext = types.ModuleType("telegram.ext")
    ext.ContextTypes = MagicMock()
    telegram.ext = ext
    return telegram


def _install_fake_deps():
    """Install minimal fakes for all command dependencies into sys.modules."""
    # Evict any stale windows.src.commands entries left by other test modules
    # (e.g. test_listener_core installs a fake package pointing at a tmp dir).
    for key in list(sys.modules):
        if key == "windows.src.commands" or key.startswith("windows.src.commands."):
            del sys.modules[key]

    fake_telegram = _make_fake_telegram()
    sys.modules.setdefault("telegram", fake_telegram)
    sys.modules.setdefault("telegram.constants", fake_telegram.constants)
    sys.modules.setdefault("telegram.ext", fake_telegram.ext)

    fake_bot_utils = types.ModuleType("windows.src.bot_utils")
    fake_bot_utils._send = AsyncMock()
    fake_bot_utils._is_allowed = lambda chat_id: chat_id == ALLOWED_CHAT_ID
    fake_bot_utils.USER_TZ = __import__("zoneinfo").ZoneInfo("Europe/Berlin")
    sys.modules["windows.src.bot_utils"] = fake_bot_utils

    # Bare-name aliases (commands may use either form)
    sys.modules.setdefault("bot_utils", fake_bot_utils)

    fake_bot_state = types.ModuleType("windows.src.bot_state")
    fake_bot_state.sessions = {}
    fake_bot_state.interrupt_pending = set()
    fake_bot_state.keepalive_paused = False
    fake_bot_state.keepalive_resume_event = None
    fake_bot_state.keepalive_last_ping_at = None
    fake_bot_state.keepalive_next_ping_at = None
    fake_bot_state.keepalive_last_ok = False
    sys.modules["windows.src.bot_state"] = fake_bot_state
    sys.modules.setdefault("bot_state", fake_bot_state)

    fake_state = types.ModuleType("windows.src.state")
    fake_state.read_scheduled_tasks = MagicMock(return_value=[])
    fake_state.write_scheduled_tasks = MagicMock()
    sys.modules["windows.src.state"] = fake_state
    sys.modules.setdefault("state", fake_state)

    fake_interrupt = types.ModuleType("windows.src.interrupt")
    fake_interrupt.handle_interrupt = AsyncMock()
    sys.modules["windows.src.interrupt"] = fake_interrupt
    sys.modules.setdefault("interrupt", fake_interrupt)

    fake_wsl_auth = types.ModuleType("windows.src.wsl_auth")
    fake_wsl_auth.diagnose_wsl_auth = AsyncMock(return_value="token ok")
    fake_wsl_auth.refresh_claude_auth = AsyncMock(return_value=True)
    sys.modules["windows.src.wsl_auth"] = fake_wsl_auth
    sys.modules.setdefault("wsl_auth", fake_wsl_auth)

    fake_bot_config = types.ModuleType("windows.src.bot_config")
    fake_bot_config._cfg = MagicMock(return_value={
        "allowed_chat_ids": [ALLOWED_CHAT_ID],
        "session": {"keepalive_interval_seconds": 3600},
    })
    fake_bot_config._reload_config = MagicMock(return_value={
        "allowed_chat_ids": [ALLOWED_CHAT_ID],
        "session": {"keepalive_interval_seconds": 3600},
    })
    sys.modules["windows.src.bot_config"] = fake_bot_config
    sys.modules.setdefault("bot_config", fake_bot_config)

    return fake_bot_utils, fake_bot_state, fake_state


def _fresh_import(module_name: str):
    """Remove module from sys.modules cache then re-import it."""
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _make_update(chat_id: int = ALLOWED_CHAT_ID):
    update = MagicMock()
    update.effective_chat.id = chat_id
    return update


def _make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot.send_message = AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# (a) Command module contract: COMMAND, DESCRIPTION, handle
# ---------------------------------------------------------------------------

EXPECTED_COMMAND_MODULES = [
    "windows.src.commands.cmd_help",
    "windows.src.commands.cmd_end",
    "windows.src.commands.cmd_tasks",
    "windows.src.commands.cmd_interrupt",
    "windows.src.commands.cmd_schedule",
    "windows.src.commands.cmd_keepalive",
    "windows.src.commands.cmd_authstatus",
    "windows.src.commands.cmd_reauth",
    "windows.src.commands.cmd_config",
    "windows.src.commands.cmd_restart",
]


@pytest.mark.parametrize("module_name", EXPECTED_COMMAND_MODULES)
def test_command_module_exports_required_attributes(module_name):
    """Each command module must export COMMAND (str), DESCRIPTION (str), handle (coroutine)."""
    _install_fake_deps()
    sys.modules.pop(module_name, None)
    mod = importlib.import_module(module_name)

    assert isinstance(mod.COMMAND, str), f"{module_name}.COMMAND must be a str"
    assert mod.COMMAND, f"{module_name}.COMMAND must not be empty"
    assert isinstance(mod.DESCRIPTION, str), f"{module_name}.DESCRIPTION must be a str"
    assert mod.DESCRIPTION, f"{module_name}.DESCRIPTION must not be empty"
    assert asyncio.iscoroutinefunction(mod.handle), f"{module_name}.handle must be a coroutine"


# ---------------------------------------------------------------------------
# (b) /help returns HTML text containing key command names
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_help_sends_html_with_command_names():
    """cmd_help.handle sends HTML that mentions the main bot commands."""
    fake_bot_utils, *_ = _install_fake_deps()
    fake_bot_utils._send = AsyncMock()

    sys.modules.pop("windows.src.commands.cmd_help", None)
    import windows.src.commands.cmd_help as cmd_help

    update = _make_update()
    ctx = _make_context()

    await cmd_help.handle(update, ctx)

    fake_bot_utils._send.assert_awaited_once()
    sent_text = fake_bot_utils._send.call_args[0][2]

    # Core commands must appear in the help text
    for cmd in ("/help", "/end", "/tasks", "/schedule"):
        assert cmd in sent_text, f"Expected '{cmd}' in help text"


@pytest.mark.asyncio
async def test_help_is_html_formatted():
    """cmd_help response must contain at least one HTML tag."""
    fake_bot_utils, *_ = _install_fake_deps()
    fake_bot_utils._send = AsyncMock()

    sys.modules.pop("windows.src.commands.cmd_help", None)
    import windows.src.commands.cmd_help as cmd_help

    await cmd_help.handle(_make_update(), _make_context())

    sent_text = fake_bot_utils._send.call_args[0][2]
    assert "<" in sent_text and ">" in sent_text, "Help response must contain HTML"


@pytest.mark.asyncio
async def test_help_ignores_disallowed_chat():
    """cmd_help.handle must silently return without sending if chat not allowed."""
    fake_bot_utils, *_ = _install_fake_deps()
    fake_bot_utils._send = AsyncMock()

    sys.modules.pop("windows.src.commands.cmd_help", None)
    import windows.src.commands.cmd_help as cmd_help

    await cmd_help.handle(_make_update(chat_id=9999), _make_context())

    fake_bot_utils._send.assert_not_awaited()


# ---------------------------------------------------------------------------
# (c) /end closes the active session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_end_removes_session():
    """cmd_end.handle removes the session from bot_state.sessions."""
    fake_bot_utils, fake_bot_state, _ = _install_fake_deps()
    fake_bot_utils._send = AsyncMock()

    fake_idle = MagicMock(spec=asyncio.Task)
    fake_idle.done.return_value = False
    fake_bot_state.sessions[ALLOWED_CHAT_ID] = {"session_id": "s1", "idle_task": fake_idle}

    sys.modules.pop("windows.src.commands.cmd_end", None)
    import windows.src.commands.cmd_end as cmd_end

    await cmd_end.handle(_make_update(), _make_context())

    assert ALLOWED_CHAT_ID not in fake_bot_state.sessions
    fake_idle.cancel.assert_called_once()
    fake_bot_utils._send.assert_awaited_once()


@pytest.mark.asyncio
async def test_end_no_session_does_not_raise():
    """cmd_end.handle must not raise if there is no active session."""
    fake_bot_utils, fake_bot_state, _ = _install_fake_deps()
    fake_bot_utils._send = AsyncMock()
    fake_bot_state.sessions.clear()

    sys.modules.pop("windows.src.commands.cmd_end", None)
    import windows.src.commands.cmd_end as cmd_end

    await cmd_end.handle(_make_update(), _make_context())  # must not raise

    fake_bot_utils._send.assert_awaited_once()


# ---------------------------------------------------------------------------
# (d) /tasks with no args returns task list or 'no tasks' message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tasks_no_args_no_tasks_returns_no_tasks_message():
    """With an empty task list, /tasks should reply with a 'no … tasks' message."""
    fake_bot_utils, _, fake_state = _install_fake_deps()
    fake_bot_utils._send = AsyncMock()
    fake_state.read_scheduled_tasks = MagicMock(return_value=[])

    sys.modules.pop("windows.src.commands.cmd_tasks", None)
    import windows.src.commands.cmd_tasks as cmd_tasks

    await cmd_tasks.handle(_make_update(), _make_context(args=[]))

    fake_bot_utils._send.assert_awaited_once()
    sent_text = fake_bot_utils._send.call_args[0][2].lower()
    assert "no" in sent_text and "task" in sent_text


@pytest.mark.asyncio
async def test_tasks_no_args_with_pending_task_lists_it():
    """With pending tasks, /tasks should list them in the reply."""
    from datetime import datetime, timezone

    fake_bot_utils, _, fake_state = _install_fake_deps()
    fake_bot_utils._send = AsyncMock()
    fake_state.read_scheduled_tasks = MagicMock(return_value=[
        {
            "id": "test-task-1",
            "status": "pending",
            "scheduled_at": "2026-06-03T10:00:00+02:00",
            "description": "Test pending task",
            "task": "test prompt",
        }
    ])

    sys.modules.pop("windows.src.commands.cmd_tasks", None)
    import windows.src.commands.cmd_tasks as cmd_tasks

    await cmd_tasks.handle(_make_update(), _make_context(args=[]))

    fake_bot_utils._send.assert_awaited_once()
    sent_text = fake_bot_utils._send.call_args[0][2]
    assert "T1" in sent_text or "test" in sent_text.lower()


@pytest.mark.asyncio
async def test_tasks_ignores_disallowed_chat():
    """cmd_tasks.handle must silently return without sending if chat not allowed."""
    fake_bot_utils, _, fake_state = _install_fake_deps()
    fake_bot_utils._send = AsyncMock()
    fake_state.read_scheduled_tasks = MagicMock(return_value=[])

    sys.modules.pop("windows.src.commands.cmd_tasks", None)
    import windows.src.commands.cmd_tasks as cmd_tasks

    await cmd_tasks.handle(_make_update(chat_id=9999), _make_context(args=[]))

    fake_bot_utils._send.assert_not_awaited()
