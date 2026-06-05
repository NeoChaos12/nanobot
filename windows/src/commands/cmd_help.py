from telegram import Update
from telegram.ext import ContextTypes

from windows.src.bot_utils import _send, _is_allowed

COMMAND     = "help"
DESCRIPTION = "List available commands"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return
    text = (
        "<b>Available commands</b>\n\n"
        "<b>Research pipeline</b>\n"
        "Any plain message → routed to the dispatcher (LLM)\n\n"
        "<b>Session control</b>\n"
        "/end — close the current session immediately\n"
        "/interrupt — signal running task to stop (send twice to force-kill)\n\n"
        "<b>Scheduled tasks</b>\n"
        "/tasks — list all queued (T1…) and failed (F1…) tasks\n"
        "/tasks queued — queued tasks only\n"
        "/tasks failed — failed tasks only\n"
        "/tasks cancel &lt;T1 or id&gt; — cancel a queued task\n"
        "/tasks retry &lt;F1 or id&gt; — requeue a failed task in 1 minute\n"
        "/tasks interrupt — gracefully stop the running session\n"
        "  (send again to force-kill)\n\n"
        "<b>Schedule a future task</b>\n"
        "/schedule &lt;offset&gt; &lt;prompt&gt;\n"
        "  offset format: <code>xdyhzm</code>  e.g. <code>2h30m</code>, <code>1d</code>, <code>45m</code>\n\n"
        "<b>OAuth keepalive</b>\n"
        "/keepalive — show keepalive status (interval, last ping, next ping)\n"
        "/keepalive pause — manually pause pings\n"
        "/keepalive resume — resume pings after manual token refresh\n\n"
        "<b>Auth</b>\n"
        "/authstatus — show WSL claude token expiry and health\n"
        "/reauth — attempt token refresh; auto-resumes keepalive on success\n\n"
        "<b>Config</b>\n"
        "/config — show hot-reloadable config values\n"
        "/config reload — reload nanobot.config.json without restarting\n\n"
        "<b>Bot management</b>\n"
        "/restart — restart the bot process (Task Scheduler relaunches)\n\n"
        "/help — show this message"
    )
    await _send(context, chat_id, text)
