import asyncio
import os

from telegram import Update
from telegram.ext import ContextTypes

from windows.src.bot_utils import _send, _is_allowed

COMMAND     = "restart"
DESCRIPTION = "Restart the bot process (Task Scheduler relaunches)"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return
    await _send(context, chat_id, "Restarting bot... back in a moment.")
    # Brief pause so the Telegram message is flushed before we kill the process.
    # os._exit(1) exits immediately with code 1, which is what Task Scheduler's
    # "restart if task fails" watches for. Using SIGTERM instead exits with code 0
    # (Python's default SIGTERM handler raises SystemExit(0)), so Task Scheduler
    # never sees a failure and never restarts.
    try:
        await asyncio.sleep(1)
    finally:
        os._exit(1)
