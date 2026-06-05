from telegram import Update
from telegram.ext import ContextTypes

import windows.src.bot_state as bot_state
from windows.src.bot_utils import _send, _is_allowed
from windows.src.wsl_auth import diagnose_wsl_auth, refresh_claude_auth

COMMAND     = "reauth"
DESCRIPTION = "Attempt token refresh or print re-auth instructions"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return

    await _send(context, chat_id, "Attempting token refresh...")
    ok = await refresh_claude_auth()
    if ok:
        was_paused = bot_state.keepalive_paused
        bot_state.keepalive_paused = False
        if bot_state.keepalive_resume_event is not None:
            bot_state.keepalive_resume_event.set()
        resume_note = " Keepalive pings resumed." if was_paused else ""
        await _send(context, chat_id, f"Token refreshed successfully. Bot is ready.{resume_note}")
    else:
        diag = await diagnose_wsl_auth()
        await _send(
            context,
            chat_id,
            (
                "<b>Auto-refresh failed.</b> Manual re-auth required.\n\n"
                f"{diag}\n\n"
                "Run this in a terminal on your machine:\n"
                "<code>wsl -d Ubuntu -- claude auth login</code>\n\n"
                "Then send <code>/reauth</code> again to verify."
            ),
        )
