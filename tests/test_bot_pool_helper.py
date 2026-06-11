"""
TDD tests for windows/src/bot_pool_helper.py.

These tests define the required behaviour of the "borrow a bot" helper that
sub-agents (task-executor / dev-loop agents) use to temporarily post progress
updates or questions into a project's Telegram group via T1/T2 (see
state/agents/dev-loop/phase2_plan.md "Reply-routing design"):

  (a) borrow_bot(project_id) picks a free pool bot (T1 or T2) per a simple
      in-state assignment table (state/bot_pool/assignments.json), marking it
      assigned to project_id. Returns None if both T1 and T2 are already
      assigned to *other* projects. Calling it again for a project that
      already holds an assignment returns that same bot (idempotent).
  (b) send_via_pool_bot(project_id, chat_id, text, pending_question_id=None)
      sends a message via the bot currently assigned to project_id, and calls
      bot_pool_routing.record_sent_message(...) when pending_question_id is
      set. Returns None if project_id has no assignment or the assigned
      bot's token is missing.
  (c) release_bot(project_id) frees project_id's assignment(s). No-op if the
      project holds no assignment.
  (d) the assignment table persists across calls -- re-read from disk on
      every call (no stale in-memory cache), mirroring project_registry's
      load_projects().

All tests use tmp_path for the assignment table, mock telegram.Bot (no real
tokens / network calls), and patch project_registry.get_bot_tokens() and
bot_pool_routing.record_sent_message().

Tests are intentionally written before the implementation so they fail on
first run (ModuleNotFoundError).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import windows.src.bot_pool_helper as helper_mod


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# (a) borrow_bot
# ---------------------------------------------------------------------------

def test_borrow_bot_assigns_first_free_bot(tmp_path):
    assignments_file = tmp_path / "assignments.json"

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file):
        result = helper_mod.borrow_bot("research-agent")

    assert result == "T1"
    saved = _read_json(assignments_file)
    assert saved["assignments"]["T1"] == "research-agent"


def test_borrow_bot_assigns_second_bot_when_first_busy(tmp_path):
    assignments_file = tmp_path / "assignments.json"
    _write_json(assignments_file, {"assignments": {"T1": "other-project"}})

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file):
        result = helper_mod.borrow_bot("research-agent")

    assert result == "T2"
    saved = _read_json(assignments_file)
    assert saved["assignments"]["T1"] == "other-project"
    assert saved["assignments"]["T2"] == "research-agent"


def test_borrow_bot_returns_none_when_both_busy(tmp_path):
    assignments_file = tmp_path / "assignments.json"
    _write_json(assignments_file, {
        "assignments": {"T1": "other-project", "T2": "another-project"},
    })

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file):
        result = helper_mod.borrow_bot("research-agent")

    assert result is None
    saved = _read_json(assignments_file)
    assert saved["assignments"] == {"T1": "other-project", "T2": "another-project"}


def test_borrow_bot_is_idempotent_for_same_project(tmp_path):
    assignments_file = tmp_path / "assignments.json"
    _write_json(assignments_file, {"assignments": {"T1": "research-agent"}})

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file):
        result = helper_mod.borrow_bot("research-agent")

    assert result == "T1"
    saved = _read_json(assignments_file)
    assert saved["assignments"] == {"T1": "research-agent"}


def test_borrow_bot_handles_missing_assignments_file(tmp_path):
    assignments_file = tmp_path / "does_not_exist" / "assignments.json"

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file):
        result = helper_mod.borrow_bot("research-agent")

    assert result == "T1"
    saved = _read_json(assignments_file)
    assert saved["assignments"]["T1"] == "research-agent"


# ---------------------------------------------------------------------------
# (b) send_via_pool_bot
# ---------------------------------------------------------------------------

def _fake_bot(message_id: int = 4821):
    bot = MagicMock()
    sent_message = MagicMock()
    sent_message.message_id = message_id
    bot.send_message = AsyncMock(return_value=sent_message)
    return bot


@pytest.mark.asyncio
async def test_send_via_pool_bot_sends_and_records(tmp_path):
    assignments_file = tmp_path / "assignments.json"
    _write_json(assignments_file, {"assignments": {"T1": "research-agent"}})

    fake_bot = _fake_bot(message_id=4821)
    fake_record = MagicMock()

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file), \
         patch.object(helper_mod.project_registry, "get_bot_tokens",
                       return_value={"dispatcher": "d-token", "T1": "t1-token", "T2": None}), \
         patch.object(helper_mod, "Bot", return_value=fake_bot) as fake_bot_cls, \
         patch.object(helper_mod.bot_pool_routing, "record_sent_message", fake_record):

        result = await helper_mod.send_via_pool_bot(
            project_id="research-agent",
            chat_id=-1001234567890,
            text="Phase 16 done, proceed?",
            pending_question_id="dev-loop-16.1-review",
        )

    fake_bot_cls.assert_called_once_with(token="t1-token")
    fake_bot.send_message.assert_awaited_once()
    _, kwargs = fake_bot.send_message.call_args
    assert kwargs["chat_id"] == -1001234567890
    assert kwargs["text"] == "Phase 16 done, proceed?"

    fake_record.assert_called_once_with(
        chat_id=-1001234567890,
        message_id=4821,
        posted_by_bot="T1",
        project_id="research-agent",
        pending_question_id="dev-loop-16.1-review",
    )

    assert result == {"bot": "T1", "message_id": 4821}


@pytest.mark.asyncio
async def test_send_via_pool_bot_skips_recording_without_pending_question(tmp_path):
    assignments_file = tmp_path / "assignments.json"
    _write_json(assignments_file, {"assignments": {"T2": "research-agent"}})

    fake_bot = _fake_bot(message_id=99)
    fake_record = MagicMock()

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file), \
         patch.object(helper_mod.project_registry, "get_bot_tokens",
                       return_value={"dispatcher": "d-token", "T1": None, "T2": "t2-token"}), \
         patch.object(helper_mod, "Bot", return_value=fake_bot), \
         patch.object(helper_mod.bot_pool_routing, "record_sent_message", fake_record):

        result = await helper_mod.send_via_pool_bot(
            project_id="research-agent",
            chat_id=-1009876543210,
            text="progress update",
        )

    fake_record.assert_not_called()
    assert result == {"bot": "T2", "message_id": 99}


@pytest.mark.asyncio
async def test_send_via_pool_bot_returns_none_without_assignment(tmp_path):
    assignments_file = tmp_path / "assignments.json"
    _write_json(assignments_file, {"assignments": {}})

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file), \
         patch.object(helper_mod.project_registry, "get_bot_tokens",
                       return_value={"dispatcher": "d-token", "T1": "t1-token", "T2": "t2-token"}), \
         patch.object(helper_mod, "Bot") as fake_bot_cls:

        result = await helper_mod.send_via_pool_bot(
            project_id="research-agent",
            chat_id=-100111,
            text="hello",
        )

    assert result is None
    fake_bot_cls.assert_not_called()


@pytest.mark.asyncio
async def test_send_via_pool_bot_returns_none_when_token_missing(tmp_path):
    assignments_file = tmp_path / "assignments.json"
    _write_json(assignments_file, {"assignments": {"T1": "research-agent"}})

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file), \
         patch.object(helper_mod.project_registry, "get_bot_tokens",
                       return_value={"dispatcher": "d-token", "T1": None, "T2": "t2-token"}), \
         patch.object(helper_mod, "Bot") as fake_bot_cls:

        result = await helper_mod.send_via_pool_bot(
            project_id="research-agent",
            chat_id=-100111,
            text="hello",
        )

    assert result is None
    fake_bot_cls.assert_not_called()


# ---------------------------------------------------------------------------
# (c) release_bot
# ---------------------------------------------------------------------------

def test_release_bot_frees_assignment(tmp_path):
    assignments_file = tmp_path / "assignments.json"
    _write_json(assignments_file, {
        "assignments": {"T1": "research-agent", "T2": "other-project"},
    })

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file):
        helper_mod.release_bot("research-agent")

    saved = _read_json(assignments_file)
    assert saved["assignments"] == {"T2": "other-project"}


def test_release_bot_noop_when_not_assigned(tmp_path):
    assignments_file = tmp_path / "assignments.json"
    _write_json(assignments_file, {"assignments": {"T1": "other-project"}})

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file):
        helper_mod.release_bot("research-agent")

    saved = _read_json(assignments_file)
    assert saved["assignments"] == {"T1": "other-project"}


def test_release_bot_handles_missing_file(tmp_path):
    assignments_file = tmp_path / "does_not_exist" / "assignments.json"

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file):
        helper_mod.release_bot("research-agent")

    saved = _read_json(assignments_file)
    assert saved["assignments"] == {}


# ---------------------------------------------------------------------------
# (d) assignment table persists / re-reads from disk on each call
# ---------------------------------------------------------------------------

def test_assignment_table_rereads_on_each_call(tmp_path):
    assignments_file = tmp_path / "assignments.json"

    with patch.object(helper_mod, "ASSIGNMENTS_FILE", assignments_file):
        first = helper_mod.borrow_bot("project-a")
        assert first == "T1"

        # Simulate an external process editing the table between calls.
        _write_json(assignments_file, {"assignments": {"T1": "project-a", "T2": "project-b"}})

        second = helper_mod.borrow_bot("project-c")

    assert second is None, (
        "borrow_bot() must re-read the assignment table from disk on each "
        "call -- stale in-memory cache detected"
    )
