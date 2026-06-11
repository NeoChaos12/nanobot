import asyncio
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

import windows.src.bot_state as bot_state
from windows.src.bot_utils import _send, _is_allowed

COMMAND     = "new"
DESCRIPTION = "End current session and start fresh (optional: /new <turns> to override history)"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return

    history_turns_override: Optional[int] = None
    if context.args:
        try:
            val = int(context.args[0])
            if val < 0:
                await _send(context, chat_id, "Turn count must be 0 or greater.")
                return
            history_turns_override = val
        except ValueError:
            await _send(context, chat_id, f"Invalid turn count: <code>{context.args[0]}</code>")
            return

    bot_state.interrupt_pending.discard(chat_id)
    session = bot_state.sessions.pop(chat_id, None)
    if session:
        idle_task: Optional[asyncio.Task] = session.get("idle_task")
        if idle_task and not idle_task.done():
            idle_task.cancel()

    bot_state.sessions[chat_id] = {
        "session_id":             None,
        "idle_task":              None,
        "history_turns_override": history_turns_override,
    }

    if history_turns_override is not None:
        msg = f"Session cleared. Next message starts fresh with {history_turns_override} history turn(s)."
    else:
        msg = "Session cleared. Next message starts fresh."
    await _send(context, chat_id, msg)
