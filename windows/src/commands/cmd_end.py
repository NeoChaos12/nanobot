import asyncio
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

import windows.src.bot_state as bot_state
from windows.src.bot_utils import _send, _is_allowed

COMMAND     = "end"
DESCRIPTION = "Close the current session immediately"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return

    bot_state.interrupt_pending.discard(chat_id)
    session = bot_state.sessions.pop(chat_id, None)
    if session:
        idle_task: Optional[asyncio.Task] = session.get("idle_task")
        if idle_task and not idle_task.done():
            idle_task.cancel()

    await _send(context, chat_id, "Session ended. Next message starts a fresh session.")
