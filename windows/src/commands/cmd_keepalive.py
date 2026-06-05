from telegram import Update
from telegram.ext import ContextTypes

import windows.src.bot_state as bot_state
from windows.src.bot_config import _cfg
from windows.src.bot_utils import _send, _is_allowed

COMMAND     = "keepalive"
DESCRIPTION = "Keepalive status/pause/resume"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /keepalive           — show keepalive status
    /keepalive pause     — manually pause pings
    /keepalive resume    — resume pings and signal the waiting loop
    """
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return

    args = context.args or []
    sub  = args[0].lower() if args else "status"

    if sub == "pause":
        bot_state.keepalive_paused = True
        await _send(context, chat_id, "Keepalive pings paused. Send /keepalive resume to restart.")
        return

    if sub == "resume":
        bot_state.keepalive_paused = False
        if bot_state.keepalive_resume_event is not None:
            bot_state.keepalive_resume_event.set()
        next_str = (
            bot_state.keepalive_next_ping_at.strftime("%H:%M UTC")
            if bot_state.keepalive_next_ping_at else "~2h from now"
        )
        await _send(context, chat_id, f"Keepalive resumed. Next ping scheduled for {next_str}.")
        return

    if sub not in ("status", ""):
        await _send(context, chat_id, "Usage: /keepalive [pause|resume]\n(no args = show status)")
        return

    # Status display
    status_str = "paused" if bot_state.keepalive_paused else "running"
    last_str   = (
        bot_state.keepalive_last_ping_at.strftime("%Y-%m-%d %H:%M UTC")
        if bot_state.keepalive_last_ping_at else "never"
    )
    last_ok_str = ("ok" if bot_state.keepalive_last_ok else "FAILED") if bot_state.keepalive_last_ping_at else "n/a"
    next_str    = (
        bot_state.keepalive_next_ping_at.strftime("%H:%M UTC")
        if bot_state.keepalive_next_ping_at else "unknown"
    )
    interval   = _cfg().get("session", {}).get("keepalive_interval_seconds", 3600)
    interval_h = interval // 3600
    interval_m = (interval % 3600) // 60

    interval_label = (
        f"{interval_h}h" if interval_m == 0
        else f"{interval_h}h {interval_m}m" if interval_h
        else f"{interval_m}m"
    )

    lines = [
        "<b>Keepalive status</b>",
        f"State:     {status_str}",
        f"Interval:  {interval_label}",
        f"Last ping: {last_str} ({last_ok_str})",
        f"Next ping: {next_str}",
    ]
    if bot_state.keepalive_paused and not bot_state.keepalive_last_ok:
        lines.append("\nPings paused after failure. After re-auth, send /keepalive resume.")
    await _send(context, chat_id, "\n".join(lines))
