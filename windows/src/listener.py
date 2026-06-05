"""
listener.py — Telegram bot entry point and session manager.

Session model:
  - First message from a chat → new dispatcher session (fresh Claude Code subprocess)
  - Subsequent messages within idle_timeout_seconds → forwarded to same session
  - Silence > idle_timeout_seconds → session closed silently; next message starts fresh
  - /interrupt at any point → handled by interrupt.py before any LLM call
  - Second /interrupt → escalate to force-kill

Scheduled tasks:
  - Agents write to state/scheduled_tasks.json to schedule deferred work
  - _scheduler_loop polls every 60s and fires due tasks as synthetic user messages
  - Pending tasks survive restarts via state file

Adding new bot commands:
  - Drop a new file in windows/src/commands/ exporting COMMAND, DESCRIPTION, and handle().
  - Restart the bot — the command is discovered and registered automatically.

Security:
  Set allowed_chat_ids in nanobot.config.json to restrict who can use the bot.
  On first run, send any message and check the logs for your chat_id, then add it.
  If left empty, any Telegram user who knows the bot token can interact with it.
"""

import asyncio
import importlib
import logging
import pkgutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import windows.src.commands as commands_pkg

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import windows.src.bot_state as bot_state
from windows.src.bot_config import _cfg, TELEGRAM_TOKEN, BASE
from windows.src.bot_utils import _send, _is_allowed
from windows.src.dispatcher import run_dispatcher
from windows.src.state import append_chat_turn

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE / "listener.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command discovery
# ---------------------------------------------------------------------------

def _load_commands() -> list:
    """Scan the commands/ package and return all modules that expose COMMAND + handle."""
    mods = []
    for _finder, name, _ispkg in pkgutil.iter_modules(commands_pkg.__path__):
        mod = importlib.import_module(f"windows.src.commands.{name}")
        if hasattr(mod, "COMMAND") and hasattr(mod, "handle"):
            mods.append(mod)
    return mods


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

async def _close_session(chat_id: int, bot) -> None:
    session = bot_state.sessions.pop(chat_id, None)
    bot_state.interrupt_pending.discard(chat_id)
    if session:
        logger.info("Session closed for chat %d (idle timeout)", chat_id)


async def _reset_idle_timer(chat_id: int, bot) -> None:
    existing  = bot_state.sessions.get(chat_id, {})
    idle_task: Optional[asyncio.Task] = existing.get("idle_task")
    if idle_task and not idle_task.done():
        idle_task.cancel()

    if chat_id not in bot_state.sessions:
        bot_state.sessions[chat_id] = {"session_id": None, "idle_task": None}

    async def _timer():
        await asyncio.sleep(_cfg().get("session", {}).get("idle_timeout_seconds", 600))
        await _close_session(chat_id, bot)

    bot_state.sessions[chat_id]["idle_task"] = asyncio.create_task(_timer())


# ---------------------------------------------------------------------------
# Message handler (non-command text → dispatcher)
# ---------------------------------------------------------------------------

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text    = (update.message.text or "").strip()

    if not text:
        return

    if not _is_allowed(chat_id):
        logger.warning("Rejected message from unlisted chat_id %d", chat_id)
        return

    if not _cfg().get("allowed_chat_ids", []):
        logger.info("Message from chat_id %d — add to allowed_chat_ids in config to restrict access", chat_id)

    bot_state.interrupt_pending.discard(chat_id)

    session    = bot_state.sessions.get(chat_id, {})
    session_id = session.get("session_id")

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        result = await run_dispatcher(
            user_message=text,
            session_id=session_id,
            chat_id=chat_id,
        )
    except Exception as exc:
        logger.error("Dispatcher error for chat %d: %s", chat_id, exc, exc_info=True)
        await _send(context, chat_id, f"⚠️ Dispatcher error: {exc}")
        return

    if chat_id not in bot_state.sessions:
        bot_state.sessions[chat_id] = {}
    new_session_id = result.get("session_id")
    bot_state.sessions[chat_id]["session_id"] = new_session_id
    await _reset_idle_timer(chat_id, context.bot)

    reply_text = result.get("text") or "(no response)"
    if new_session_id:
        try:
            append_chat_turn(chat_id, new_session_id, "user", text)
            append_chat_turn(chat_id, new_session_id, "assistant", reply_text)
        except Exception as exc:
            logger.warning("Failed to log chat turn: %s", exc)

    await _send(context, chat_id, reply_text)


# ---------------------------------------------------------------------------
# OAuth keepalive ping + loop
# ---------------------------------------------------------------------------

async def _keepalive_ping() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "wsl.exe", "bash", "-l", "-c",
            'claude -p --dangerously-skip-permissions "say Hi!"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=90)
        if proc.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            logger.warning("Keepalive ping failed (rc=%d): %s", proc.returncode, stderr[:200])
        return proc.returncode == 0
    except asyncio.TimeoutError:
        logger.error("Keepalive ping timed out after 90s")
        return False
    except Exception as exc:
        logger.error("Keepalive ping error: %s", exc)
        return False


async def _keepalive_loop(app: "Application") -> None:
    interval = _cfg().get("session", {}).get("keepalive_interval_seconds", 3600)
    logger.info("Keepalive loop started (interval: %ds)", interval)

    while True:
        interval = _cfg().get("session", {}).get("keepalive_interval_seconds", 3600)
        bot_state.keepalive_next_ping_at = (
            datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=interval)
        )
        await asyncio.sleep(interval)

        if bot_state.keepalive_paused:
            logger.info("Keepalive is paused; skipping ping")
            continue

        logger.info("Running keepalive ping")
        ok = await _keepalive_ping()
        bot_state.keepalive_last_ping_at = datetime.now(timezone.utc).replace(microsecond=0)
        bot_state.keepalive_last_ok      = ok

        if ok:
            logger.info("Keepalive ping succeeded at %s", bot_state.keepalive_last_ping_at.isoformat())
        else:
            logger.warning("Keepalive ping failed — pausing keepalive and notifying user")
            bot_state.keepalive_paused = True

            chat_id = next(iter(_cfg().get("allowed_chat_ids", [])), None)
            if chat_id:
                try:
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "⚠️ <b>Keepalive failed</b> — Claude OAuth token may have expired.\n\n"
                            "Re-authenticate in WSL:\n"
                            "<code>wsl -d Ubuntu -- claude auth login</code>\n\n"
                            "Then send <code>/keepalive resume</code> to restart pings, "
                            "or <code>/reauth</code> to let the bot verify the new token."
                        ),
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    logger.error("Failed to send keepalive failure notification: %s", exc)

            if bot_state.keepalive_resume_event is not None:
                bot_state.keepalive_resume_event.clear()
                logger.info("Keepalive waiting for resume signal")
                await bot_state.keepalive_resume_event.wait()
                logger.info("Keepalive resumed — returning to normal schedule")
                _resume_interval = _cfg().get("session", {}).get("keepalive_interval_seconds", 3600)
                bot_state.keepalive_next_ping_at = (
                    datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=_resume_interval)
                )


# ---------------------------------------------------------------------------
# Scheduled task poller
# ---------------------------------------------------------------------------

async def _scheduler_loop(app: "Application") -> None:
    from windows.src.state import read_scheduled_tasks, write_scheduled_tasks

    poll_interval = _cfg().get("session", {}).get("scheduler_poll_interval_seconds", 60)
    logger.info("Scheduler loop started (poll interval: %ds)", poll_interval)

    while True:
        poll_interval = _cfg().get("session", {}).get("scheduler_poll_interval_seconds", 60)
        await asyncio.sleep(poll_interval)
        try:
            now   = datetime.now(timezone.utc)
            tasks = read_scheduled_tasks()
            changed = False

            for task in tasks:
                if task.get("status") != "pending":
                    continue
                scheduled_at = task.get("scheduled_at", "")
                try:
                    fire_time = datetime.fromisoformat(scheduled_at)
                except (ValueError, TypeError):
                    continue

                if now >= fire_time:
                    if task.get("fired_at"):
                        continue

                    chat_id   = task.get("chat_id") or next(iter(_cfg().get("allowed_chat_ids", [])), None)
                    task_text = task.get("task", "")
                    task_id   = task.get("id", "?")
                    logger.info("Firing scheduled task %s for chat %s", task_id, chat_id)

                    task["fired_at"] = now.isoformat()
                    write_scheduled_tasks(tasks)

                    if chat_id and task_text and chat_id in (_cfg().get("allowed_chat_ids") or {chat_id}):
                        is_dev_loop = "DEV LOOP" in task_text
                        try:
                            result = await run_dispatcher(
                                user_message=f"[Scheduled task {task_id}] {task_text}",
                                session_id=None,
                                chat_id=chat_id,
                            )
                            text = result.get("text") or "(no response)"

                            if is_dev_loop:
                                from windows.src.dispatcher import dev_loop_lifecycle
                                from windows.src.bot_utils import USER_TZ
                                async def _task_send(cid, msg):
                                    await app.bot.send_message(cid, msg, parse_mode="HTML")
                                await dev_loop_lifecycle(
                                    output_text=text,
                                    stderr_text=result.get("_stderr", ""),
                                    clean_exit=not result.get("error", False),
                                    now_berlin=datetime.now(USER_TZ),
                                    chat_id=chat_id,
                                    send_message=_task_send,
                                )

                            if result.get("error"):
                                task["status"]     = "failed"
                                task["last_error"] = text[:200]
                                logger.warning("Scheduled task %s subprocess error: %s", task_id, text[:100])
                                if not is_dev_loop:
                                    await app.bot.send_message(
                                        chat_id=chat_id,
                                        text=(
                                            f"⚠️ Scheduled task <code>{task_id}</code> failed:\n"
                                            f"{text[:500]}\n\n"
                                            f"Use /tasks retry {task_id} to requeue it."
                                        ),
                                        parse_mode="HTML",
                                    )
                            else:
                                task["status"] = "fired"
                                for i in range(0, max(len(text), 1), 4096):
                                    await app.bot.send_message(
                                        chat_id=chat_id,
                                        text=text[i:i + 4096],
                                        parse_mode="HTML",
                                    )
                        except Exception as exc:
                            task["status"]     = "failed"
                            task["last_error"] = str(exc)
                            logger.error("Scheduled task %s failed: %s", task_id, exc, exc_info=True)
                            try:
                                await app.bot.send_message(
                                    chat_id=chat_id,
                                    text=(
                                        f"⚠️ Scheduled task <code>{task_id}</code> failed: {exc}\n"
                                        f"Use /tasks retry {task_id} to requeue it."
                                    ),
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass
                    else:
                        task["status"]     = "failed"
                        task["last_error"] = "missing or disallowed chat_id"
                    changed = True

            if changed:
                write_scheduled_tasks(tasks)

        except Exception as exc:
            logger.error("Scheduler loop error: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("Starting Nanobot (idle timeout: %ds)",
                _cfg().get("session", {}).get("idle_timeout_seconds", 600))
    logger.info("Allowed chat IDs: %s", _cfg().get("allowed_chat_ids") or "unrestricted")

    cmd_modules = _load_commands()
    logger.info("Loaded %d command(s): %s", len(cmd_modules), [m.COMMAND for m in cmd_modules])

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    for mod in cmd_modules:
        app.add_handler(CommandHandler(mod.COMMAND, mod.handle))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    async def _post_init(application: "Application") -> None:
        bot_state.keepalive_resume_event = asyncio.Event()

        bot_commands = [
            BotCommand(mod.COMMAND, mod.DESCRIPTION)
            for mod in cmd_modules
            if hasattr(mod, "DESCRIPTION")
        ]
        await application.bot.set_my_commands(bot_commands)
        asyncio.create_task(_scheduler_loop(application))
        asyncio.create_task(_keepalive_loop(application))

        startup_chat_id = next(iter(_cfg().get("allowed_chat_ids", [])), None)
        if startup_chat_id:
            await application.bot.send_message(
                chat_id=startup_chat_id,
                text="Bot started.",
                parse_mode="HTML",
            )

    app.post_init = _post_init

    logger.info("Polling for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
