"""
bot_pool_routing.py -- Sent-message registry and reply-routing for the bot pool.

Background: in the bot-pool deployment, PADispatcher_bot is the only bot that
reads Archit's messages in a project's Telegram group. T1/T2 are normally
silent but can be temporarily borrowed by a sub-agent to post progress
updates or questions into the group on its behalf. When Archit later replies,
PADispatcher_bot needs to route that reply to the right pending question /
sub-agent state -- even though it didn't post the original message, and T1/T2
may have been reassigned to a different project since.

Routing decision order (see state/agents/dev-loop/phase2_plan.md
"Reply-routing design" for the full design):

  1. reply-to-tracked-message -- if the incoming message is a Telegram reply
     to a message recorded via record_sent_message(), resolve_reply() returns
     that registry entry (and hence its pending_question_id) regardless of
     which bot currently holds the T1/T2 slot.
  2. single-open-question -- if there's no reply match, resolve_followup()
     looks at the project's pending_questions.json. Exactly one open question
     -> route the message there.
  3. most-recent-open-with-note -- multiple open questions -> resolve_followup()
     still returns the most recently asked one, flagged via "multiple_open" so
     the caller can post a one-line note that Archit can override by replying
     to a specific message.
  4. normal dispatch -- zero open questions -> resolve_followup() returns None
     and the caller falls through to the project's normal dispatcher flow.

The sent-message registry (state/bot_pool/sent_messages.json) is shared across
all projects -- (chat_id, message_id) pairs are globally unique within
Telegram, so no per-project namespacing is needed. Entries are pruned once
their pending_question_id is answered/expired, or once they exceed
max_age_days (default 7), mirroring the chat_history pruning policy.

Path layout (relative to this file's location):
  windows/src/bot_pool_routing.py  ->  parent.parent.parent = <project_root>
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from windows.src.state import atomic_write, read_json
from windows.src import project_registry

BASE = Path(__file__).parent.parent.parent
SENT_MESSAGES_FILE = BASE / "state" / "bot_pool" / "sent_messages.json"

# pending_questions.json statuses considered "still open" for follow-up routing.
# "blocked_user" is included alongside the schema's "pending"/"buffered" since
# dev_todo-style tasks may surface their pending questions with that status.
OPEN_QUESTION_STATUSES = {"pending", "buffered", "blocked_user"}

DEFAULT_MAX_AGE_DAYS = 7


def record_sent_message(
    chat_id: int,
    message_id: int,
    posted_by_bot: str,
    project_id: str,
    pending_question_id: Optional[str],
) -> dict:
    """Append an entry to the sent-message registry and return it."""
    entries = read_json(SENT_MESSAGES_FILE)
    entry = {
        "chat_id": chat_id,
        "message_id": message_id,
        "posted_by_bot": posted_by_bot,
        "project_id": project_id,
        "pending_question_id": pending_question_id,
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }
    entries.append(entry)
    atomic_write(SENT_MESSAGES_FILE, entries)
    return entry


def resolve_reply(chat_id: int, reply_to_message_id: int) -> Optional[dict]:
    """Return the registry entry for (chat_id, reply_to_message_id), or None."""
    for entry in read_json(SENT_MESSAGES_FILE):
        if entry.get("chat_id") == chat_id and entry.get("message_id") == reply_to_message_id:
            return entry
    return None


def _project_state_dir(project_id: str, chat_id: Optional[int] = None) -> Optional[str]:
    """Look up project_id's state_dir, optionally validating chat_id matches."""
    registry = project_registry.load_projects()
    entry = registry.get("projects", {}).get(project_id)
    if entry is None:
        return None
    if chat_id is not None and entry.get("chat_id") != chat_id:
        return None
    return entry.get("state_dir")


def resolve_followup(chat_id: int, project_id: str) -> Optional[dict]:
    """Return the most recent open pending question for project_id, or None.

    Returns {"pending_question_id": <id>, "multiple_open": <bool>} if at
    least one open question exists, else None.
    """
    state_dir = _project_state_dir(project_id, chat_id)
    if state_dir is None:
        return None

    questions = read_json(Path(state_dir) / "pending_questions.json")
    open_qs = [q for q in questions if q.get("status") in OPEN_QUESTION_STATUSES]
    if not open_qs:
        return None

    open_qs.sort(key=lambda q: q.get("asked_at", ""), reverse=True)
    return {
        "pending_question_id": open_qs[0]["id"],
        "multiple_open": len(open_qs) > 1,
    }


def _is_resolved(entry: dict) -> bool:
    """True if entry's pending_question_id is answered/expired (or untraceable)."""
    pending_question_id = entry.get("pending_question_id")
    if not pending_question_id:
        return False

    state_dir = _project_state_dir(entry.get("project_id"))
    if state_dir is None:
        return False

    questions = read_json(Path(state_dir) / "pending_questions.json")
    for q in questions:
        if q.get("id") == pending_question_id:
            return q.get("status") in ("answered", "expired")
    return False


def prune_sent_messages(max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> list[dict]:
    """Remove resolved or stale entries from the sent-message registry.

    An entry is removed if its pending_question_id is answered/expired, or if
    its posted_at is older than max_age_days. Returns the kept entries.
    """
    entries = read_json(SENT_MESSAGES_FILE)
    if not entries:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    kept = []
    for entry in entries:
        posted_at = entry.get("posted_at")
        try:
            posted_dt = datetime.fromisoformat(posted_at)
        except (TypeError, ValueError):
            posted_dt = None
        if posted_dt is not None:
            if posted_dt.tzinfo is None:
                posted_dt = posted_dt.replace(tzinfo=timezone.utc)
            if posted_dt < cutoff:
                continue
        if _is_resolved(entry):
            continue
        kept.append(entry)

    atomic_write(SENT_MESSAGES_FILE, kept)
    return kept
