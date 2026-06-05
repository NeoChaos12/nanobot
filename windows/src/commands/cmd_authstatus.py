from telegram import Update
from telegram.ext import ContextTypes

from windows.src.bot_utils import _send, _is_allowed
from windows.src.wsl_auth import diagnose_wsl_auth

COMMAND     = "authstatus"
DESCRIPTION = "Show WSL claude token expiry and health"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return
    diag = await diagnose_wsl_auth()
    await _send(context, chat_id, f"<b>WSL claude auth status</b>\n{diag}")
