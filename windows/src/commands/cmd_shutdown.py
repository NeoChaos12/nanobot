import asyncio
import os

from telegram import Update
from telegram.ext import ContextTypes

from windows.src.bot_utils import _send, _is_allowed

COMMAND     = "shutdown"
DESCRIPTION = "Stop the bot (clean exit, no auto-restart)"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return
    await _send(
        context, chat_id,
        "Shutting down. Restart manually via Task Scheduler when you're ready.",
    )

    # Brief pause so the Telegram message is flushed before we exit.
    # Exit 0 (unlike /restart's exit 1) -- Task Scheduler's RestartInterval
    # only fires on non-zero exit, so this is a clean stop with no auto-restart.
    try:
        await asyncio.sleep(1)
    finally:
        os._exit(0)
