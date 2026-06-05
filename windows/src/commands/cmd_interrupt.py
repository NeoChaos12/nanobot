from telegram import Update
from telegram.ext import ContextTypes

import windows.src.bot_state as bot_state
from windows.src.bot_utils import _send, _is_allowed
from windows.src.interrupt import handle_interrupt

COMMAND     = "interrupt"
DESCRIPTION = "Signal running task to stop (send twice to force-kill)"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return

    is_second = chat_id in bot_state.interrupt_pending

    async def send(text: str):
        await _send(context, chat_id, text)

    if is_second:
        bot_state.interrupt_pending.discard(chat_id)
        await handle_interrupt(send, force=True)
        bot_state.sessions.pop(chat_id, None)
    else:
        bot_state.interrupt_pending.add(chat_id)
        await handle_interrupt(send, force=False)
