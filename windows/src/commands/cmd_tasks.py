import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

import windows.src.bot_state as bot_state
from windows.src.bot_utils import _send, _is_allowed, USER_TZ
from windows.src.interrupt import handle_interrupt
from windows.src.state import read_scheduled_tasks, write_scheduled_tasks

COMMAND     = "tasks"
DESCRIPTION = "List tasks: /tasks [queued|failed|retry Fn|cancel Tn|interrupt]"


def _build_short_id_map(tasks: list[dict]) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    t_idx = f_idx = 1
    for task in tasks:
        status = task.get("status")
        if status == "pending":
            mapping[f"T{t_idx}"] = task
            t_idx += 1
        elif status == "failed":
            mapping[f"F{f_idx}"] = task
            f_idx += 1
    return mapping


def _resolve_task(ref: str, tasks: list[dict]) -> Optional[dict]:
    short_map = _build_short_id_map(tasks)
    if ref.upper() in short_map:
        return short_map[ref.upper()]
    for t in tasks:
        if t.get("id") == ref:
            return t
    return None


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /tasks [queued|failed]
    /tasks retry <id>      — requeue a failed task (short ID F1 or slug)
    /tasks cancel <id>     — cancel a queued task  (short ID T1 or slug)
    /tasks interrupt       — gracefully signal the running session to stop
    """
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return

    args = context.args or []
    sub  = args[0].lower() if args else ""

    if sub == "retry":
        if len(args) < 2:
            await _send(context, chat_id, "Usage: /tasks retry &lt;F1 or task-id&gt;")
            return
        ref     = args[1]
        tasks   = read_scheduled_tasks()
        matched = _resolve_task(ref, tasks)
        if matched is None:
            await _send(context, chat_id, f"No task found: <code>{ref}</code>")
            return
        if matched.get("status") != "failed":
            await _send(context, chat_id,
                        f"Task <code>{ref}</code> has status <b>{matched.get('status')}</b> — only failed tasks can be retried.")
            return
        retry_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1)
        matched["status"]       = "pending"
        matched["scheduled_at"] = retry_at.isoformat()
        matched.pop("last_error", None)
        matched.pop("fired_at",   None)
        write_scheduled_tasks(tasks)
        label      = matched.get("description") or matched.get("task", "")[:80]
        retry_local = retry_at.astimezone(USER_TZ)
        await _send(context, chat_id,
                    f"Requeued <code>{ref}</code> — {label}\nFires at {retry_local.strftime('%H:%M:%S %Z')}.")
        return

    if sub == "cancel":
        if len(args) < 2:
            await _send(context, chat_id, "Usage: /tasks cancel &lt;T1 or task-id&gt;")
            return
        ref     = args[1]
        tasks   = read_scheduled_tasks()
        matched = _resolve_task(ref, tasks)
        if matched is None:
            await _send(context, chat_id, f"No task found: <code>{ref}</code>")
            return
        if matched.get("status") != "pending":
            await _send(context, chat_id,
                        f"Task <code>{ref}</code> is already <b>{matched.get('status')}</b>.")
            return
        matched["status"] = "cancelled"
        write_scheduled_tasks(tasks)
        label = matched.get("description") or matched.get("task", "")[:80]
        await _send(context, chat_id, f"Cancelled <code>{ref}</code> — {label}")
        return

    if sub == "interrupt":
        is_second = chat_id in bot_state.interrupt_pending

        async def _task_send(text: str):
            await _send(context, chat_id, text)

        if is_second:
            bot_state.interrupt_pending.discard(chat_id)
            await handle_interrupt(_task_send, force=True)
            bot_state.sessions.pop(chat_id, None)
        else:
            bot_state.interrupt_pending.add(chat_id)
            await handle_interrupt(_task_send, force=False)
        return

    # Display (filter or full list)
    all_tasks = read_scheduled_tasks()
    pending   = [t for t in all_tasks if t.get("status") == "pending"]
    failed    = [t for t in all_tasks if t.get("status") == "failed"]

    show_queued = sub in ("", "queued")
    show_failed = sub in ("", "failed")

    if sub not in ("", "queued", "failed"):
        await _send(context, chat_id,
                    f"Unknown sub-command <code>{sub}</code>. Try /tasks, /tasks queued, /tasks failed, "
                    "/tasks retry &lt;id&gt;, /tasks cancel &lt;id&gt;, or /tasks interrupt.")
        return

    if not (show_queued and pending) and not (show_failed and failed):
        noun = "queued" if sub == "queued" else ("failed" if sub == "failed" else "scheduled")
        await _send(context, chat_id, f"No {noun} tasks.")
        return

    short_map   = _build_short_id_map(all_tasks)
    reverse_map = {id(v): k for k, v in short_map.items()}

    lines: list[str] = []
    if show_queued and pending:
        lines.append("<b>Queued</b> — cancel with /tasks cancel T&lt;n&gt;")
        for t in pending:
            sid    = reverse_map.get(id(t), "?")
            raw_at = t.get("scheduled_at") or ""
            try:
                fire_local = datetime.fromisoformat(raw_at).astimezone(USER_TZ)
                fire_time  = fire_local.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                fire_time = raw_at[:16].replace("T", " ")
            label = t.get("description") or t.get("task", "")[:80]
            lines.append(f"  <code>{sid}</code>  {fire_time}  {label}")

    if show_failed and failed:
        lines.append("<b>Failed</b> — retry with /tasks retry F&lt;n&gt;")
        for t in failed:
            sid   = reverse_map.get(id(t), "?")
            label = t.get("description") or t.get("task", "")[:80]
            error = t.get("last_error", "unknown error")[:60]
            lines.append(f"  <code>{sid}</code>  {label} — <i>{error}</i>")

    await _send(context, chat_id, "\n".join(lines))
