"""
Integration smoke tests for the bot runtime kernel.

Coverage:
  (a) telegram.ext.Application can be built without error using a mock token
  (b) _load_commands discovers all 10 cmd_*.py modules from windows.src.commands
  (c) cmd_help.handle responds with non-empty HTML text (end-to-end command path)

No real Telegram API calls are made — all network/bot operations are mocked.
"""

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Install fakes shared across all smoke tests
# ---------------------------------------------------------------------------

ALLOWED_CHAT_ID = 42


def _install_runtime_fakes():
    """
    Install minimal fakes for everything windows.src.listener and the commands
    need — telegram, bot_config, bot_state, bot_utils, state, etc.
    Returns (fake_telegram, fake_bot_utils, fake_bot_state).
    """
    # ---- telegram ----
    fake_telegram = types.ModuleType("telegram")
    fake_telegram.BotCommand = MagicMock()
    fake_telegram.Update = MagicMock()
    fake_telegram.constants = types.ModuleType("telegram.constants")
    fake_telegram.constants.ParseMode = MagicMock()
    ext = types.ModuleType("telegram.ext")

    fake_app_builder = MagicMock()
    fake_app = MagicMock()
    fake_app_builder.token.return_value = fake_app_builder
    fake_app_builder.build.return_value = fake_app
    ext.Application = MagicMock()
    ext.Application.builder.return_value = fake_app_builder
    ext.CommandHandler = MagicMock(side_effect=lambda cmd, hdl: MagicMock())
    ext.MessageHandler = MagicMock(side_effect=lambda flt, hdl: MagicMock())
    ext.ContextTypes = MagicMock()
    ext.filters = MagicMock()
    fake_telegram.ext = ext
    sys.modules["telegram"] = fake_telegram
    sys.modules["telegram.constants"] = fake_telegram.constants
    sys.modules["telegram.ext"] = ext

    # ---- bot_config ----
    fake_bot_config = types.ModuleType("windows.src.bot_config")
    fake_bot_config.TELEGRAM_TOKEN = "123456:FAKE-TOKEN"
    fake_bot_config.BASE = Path(__file__).parent.parent / "windows"
    fake_bot_config._cfg = MagicMock(return_value={
        "allowed_chat_ids": [ALLOWED_CHAT_ID],
        "session": {"idle_timeout_seconds": 600, "keepalive_interval_seconds": 3600},
    })
    fake_bot_config._reload_config = MagicMock(return_value={})
    sys.modules["windows.src.bot_config"] = fake_bot_config
    sys.modules.setdefault("bot_config", fake_bot_config)

    # ---- bot_state ----
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

    # ---- bot_utils ----
    fake_bot_utils = types.ModuleType("windows.src.bot_utils")
    fake_bot_utils._send = AsyncMock()
    fake_bot_utils._is_allowed = lambda chat_id: chat_id == ALLOWED_CHAT_ID
    fake_bot_utils.USER_TZ = __import__("zoneinfo").ZoneInfo("Europe/Berlin")
    sys.modules["windows.src.bot_utils"] = fake_bot_utils
    sys.modules.setdefault("bot_utils", fake_bot_utils)

    # ---- state ----
    fake_state = types.ModuleType("windows.src.state")
    fake_state.append_chat_turn = MagicMock()
    fake_state.read_scheduled_tasks = MagicMock(return_value=[])
    fake_state.write_scheduled_tasks = MagicMock()
    fake_state.compact_snapshot = MagicMock(return_value="{}")
    sys.modules["windows.src.state"] = fake_state
    sys.modules.setdefault("state", fake_state)

    # ---- dispatcher ----
    fake_dispatcher = types.ModuleType("windows.src.dispatcher")
    fake_dispatcher.run_dispatcher = AsyncMock(return_value={"text": "ok", "session_id": "s1"})
    fake_dispatcher.dev_loop_lifecycle = AsyncMock()
    sys.modules["windows.src.dispatcher"] = fake_dispatcher

    # ---- interrupt / wsl_auth ----
    fake_interrupt = types.ModuleType("windows.src.interrupt")
    fake_interrupt.handle_interrupt = AsyncMock()
    sys.modules["windows.src.interrupt"] = fake_interrupt
    sys.modules.setdefault("interrupt", fake_interrupt)

    fake_wsl_auth = types.ModuleType("windows.src.wsl_auth")
    fake_wsl_auth.diagnose_wsl_auth = AsyncMock(return_value="token ok")
    fake_wsl_auth.refresh_claude_auth = AsyncMock(return_value=True)
    sys.modules["windows.src.wsl_auth"] = fake_wsl_auth
    sys.modules.setdefault("wsl_auth", fake_wsl_auth)

    return ext, fake_bot_utils, fake_bot_state


# ---------------------------------------------------------------------------
# (a) Application builds successfully
# ---------------------------------------------------------------------------

def test_application_builder_succeeds():
    """Application.builder().token(...).build() completes without error."""
    ext, *_ = _install_runtime_fakes()

    from telegram.ext import Application
    app = Application.builder().token("FAKE-TOKEN").build()
    assert app is not None


# ---------------------------------------------------------------------------
# (b) _load_commands discovers all command modules
# ---------------------------------------------------------------------------

def test_load_commands_discovers_all_command_modules():
    """_load_commands returns one entry per cmd_*.py file in windows.src.commands."""
    _install_runtime_fakes()

    # Evict stale cached commands to ensure a clean import
    for key in list(sys.modules):
        if key.startswith("windows.src.commands") or "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener
    modules = listener._load_commands()

    commands = {m.COMMAND for m in modules}
    expected = {"help", "end", "tasks", "interrupt", "schedule",
                "keepalive", "authstatus", "reauth", "config", "restart"}
    assert expected == commands, f"Missing commands: {expected - commands}"


def test_load_commands_all_have_handle_coroutine():
    """Every discovered command module's handle attribute must be a coroutine."""
    import asyncio

    _install_runtime_fakes()
    for key in list(sys.modules):
        if key.startswith("windows.src.commands") or "listener" in key:
            del sys.modules[key]

    import windows.src.listener as listener
    for mod in listener._load_commands():
        assert asyncio.iscoroutinefunction(mod.handle), (
            f"windows.src.commands.{mod.COMMAND}.handle is not a coroutine"
        )


# ---------------------------------------------------------------------------
# (c) /help handler responds with HTML text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_help_handler_returns_html_text():
    """cmd_help.handle sends a non-empty HTML response through the _send mock."""
    _, fake_bot_utils, _ = _install_runtime_fakes()
    fake_bot_utils._send = AsyncMock()

    for key in list(sys.modules):
        if key.startswith("windows.src.commands.cmd_help"):
            del sys.modules[key]

    import windows.src.commands.cmd_help as cmd_help

    update = MagicMock()
    update.effective_chat.id = ALLOWED_CHAT_ID
    ctx = MagicMock()
    ctx.args = []

    await cmd_help.handle(update, ctx)

    fake_bot_utils._send.assert_awaited_once()
    text = fake_bot_utils._send.call_args[0][2]
    assert text, "help response must not be empty"
    assert "<b>" in text or "<code>" in text, "help response must contain HTML"
