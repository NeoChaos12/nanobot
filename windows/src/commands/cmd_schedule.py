import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from windows.src.bot_utils import _send, _is_allowed, USER_TZ
from windows.src.state import read_scheduled_tasks, write_scheduled_tasks

COMMAND     = "schedule"
DESCRIPTION = "Queue a future task: /schedule <offset> <prompt>"


def _parse_offset(offset_str: str) -> Optional[timedelta]:
    """
    Parse an offset string of the form xdyhzm (any subset, any order).
    Returns a timedelta or None if unparseable.
    """
    pattern = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$", re.IGNORECASE)
    m = pattern.match(offset_str.strip())
    if not m or not any(m.groups()):
        return None
    days    = int(m.group(1) or 0)
    hours   = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    if days == hours == minutes == 0:
        return None
    return timedelta(days=days, hours=hours, minutes=minutes)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /schedule <offset> <prompt>
    Queue a task that fires <offset> from now and sends <prompt> to the agent.
    Offset format: xdyhzm  e.g. 1d2h30m, 2h, 30m
    """
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return

    args = context.args or []
    if len(args) < 2:
        await _send(context, chat_id,
                    "Usage: /schedule &lt;offset&gt; &lt;prompt&gt;\n"
                    "Offset format: <code>xdyhzm</code>  e.g. <code>1d2h30m</code>, <code>2h</code>, <code>30m</code>")
        return

    offset_str = args[0]
    delta      = _parse_offset(offset_str)
    if delta is None:
        await _send(context, chat_id,
                    f"Can't parse offset <code>{offset_str}</code>. "
                    "Use <code>xdyhzm</code> format, e.g. <code>1d</code>, <code>2h30m</code>, <code>45m</code>.")
        return

    prompt     = " ".join(args[1:])
    now        = datetime.now(timezone.utc).replace(microsecond=0)
    fire_at    = now + delta
    short_slug = fire_at.strftime("%Y%m%d-%H%M")
    uid        = uuid.uuid4().hex[:6]
    task_id    = f"sched-{short_slug}-{uid}"
    description = prompt[:80]

    tasks = read_scheduled_tasks()
    tasks.append({
        "id":           task_id,
        "created_at":   now.isoformat(),
        "scheduled_at": fire_at.isoformat(),
        "task":         prompt,
        "description":  description,
        "chat_id":      chat_id,
        "status":       "pending",
    })
    write_scheduled_tasks(tasks)

    fire_local = fire_at.astimezone(USER_TZ)
    fire_str   = fire_local.strftime("%Y-%m-%d %H:%M (%Z)")
    await _send(context, chat_id,
                f"Scheduled for <b>{fire_str}</b>\n<code>{task_id}</code>\n{description}")
