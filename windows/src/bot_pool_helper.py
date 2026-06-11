"""
bot_pool_helper.py -- "Borrow a bot" helper for sub-agents (Phase 16).

Background: T1/T2 (PABotPoolT1_bot / PABotPoolT2_bot) are normally silent in
a project's Telegram group. A sub-agent (task-executor / dev-loop agent) can
temporarily borrow one of them to post a progress update or question into the
group on its behalf. This module provides that borrow/send/release cycle:

  - borrow_bot(project_id) assigns a free pool bot (T1 or T2) to project_id,
    persisted in state/bot_pool/assignments.json. Idempotent: a project that
    already holds an assignment gets the same bot back. Returns None if both
    T1 and T2 are assigned to other projects.
  - send_via_pool_bot(project_id, chat_id, text, pending_question_id=None)
    sends `text` via the bot currently assigned to project_id. If
    pending_question_id is given, records the sent message in the
    bot_pool_routing sent-message registry (Phase 14) so a later reply from
    Archit can be routed back to that question.
  - release_bot(project_id) frees project_id's assignment(s) once the
      sub-agent is done with the borrowed bot.

Usage for sub-agents (dev-loop / task-executor):
    bot_name = bot_pool_helper.borrow_bot(project_id)
    if bot_name:
        await bot_pool_helper.send_via_pool_bot(
            project_id, chat_id, "Phase 16 done -- proceed?",
            pending_question_id="dev-loop-16.1-review",
        )
        # ... later, once the borrowed bot is no longer needed:
        bot_pool_helper.release_bot(project_id)
    # If borrow_bot() returns None, both T1/T2 are busy with other projects --
    # fall back to writing a pending question without a Telegram post; it will
    # still surface via the project's normal pending_questions.json flow.

The assignment table is re-read from disk on every call (no stale in-memory
cache), mirroring project_registry.load_projects().

Path layout (relative to this file's location):
  windows/src/bot_pool_helper.py  ->  parent.parent.parent = <project_root>
"""

from pathlib import Path
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode

from windows.src.state import atomic_write, read_json
from windows.src import project_registry
from windows.src import bot_pool_routing

BASE = Path(__file__).parent.parent.parent
ASSIGNMENTS_FILE = BASE / "state" / "bot_pool" / "assignments.json"

POOL_BOT_NAMES = ("T1", "T2")


def _load_assignments() -> dict:
    data = read_json(ASSIGNMENTS_FILE)
    if not isinstance(data, dict) or "assignments" not in data:
        return {"assignments": {}}
    return data


def borrow_bot(project_id: str) -> Optional[str]:
    """Assign a free pool bot (T1 or T2) to project_id and return its name.

    Idempotent: if project_id already holds an assignment, returns that bot
    without changing the table. Returns None if both T1 and T2 are assigned
    to other projects.
    """
    state = _load_assignments()
    assignments = state["assignments"]

    for name, assigned_project in assignments.items():
        if assigned_project == project_id:
            return name

    for name in POOL_BOT_NAMES:
        if name not in assignments:
            assignments[name] = project_id
            atomic_write(ASSIGNMENTS_FILE, state)
            return name

    return None


def release_bot(project_id: str) -> None:
    """Free project_id's pool-bot assignment(s), if any. No-op otherwise."""
    state = _load_assignments()
    assignments = state["assignments"]
    for name in [n for n, p in assignments.items() if p == project_id]:
        del assignments[name]
    atomic_write(ASSIGNMENTS_FILE, state)


async def send_via_pool_bot(
    project_id: str,
    chat_id: int,
    text: str,
    pending_question_id: Optional[str] = None,
) -> Optional[dict]:
    """Send `text` to chat_id via the pool bot assigned to project_id.

    Returns {"bot": <"T1"|"T2">, "message_id": <int>} on success, or None if
    project_id has no assignment or the assigned bot's token is unavailable.

    If pending_question_id is set, records the sent message via
    bot_pool_routing.record_sent_message() so a later reply can be routed
    back to that question.
    """
    assignments = _load_assignments()["assignments"]

    bot_name = next((n for n, p in assignments.items() if p == project_id), None)
    if bot_name is None:
        return None

    token = project_registry.get_bot_tokens().get(bot_name)
    if not token:
        return None

    bot = Bot(token=token)
    sent_message = await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

    if pending_question_id is not None:
        bot_pool_routing.record_sent_message(
            chat_id=chat_id,
            message_id=sent_message.message_id,
            posted_by_bot=bot_name,
            project_id=project_id,
            pending_question_id=pending_question_id,
        )

    return {"bot": bot_name, "message_id": sent_message.message_id}
