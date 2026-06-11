"""
TDD tests for windows/src/bot_pool_routing.py.

These tests define the required behaviour of the sent-message registry and
reply-routing logic for the bot pool (see state/agents/dev-loop/phase2_plan.md
"Reply-routing design"):

  (a) record_sent_message(...) appends an entry to
      state/bot_pool/sent_messages.json
  (b) resolve_reply(chat_id, reply_to_message_id) returns the matching
      registry entry, or None
  (c) resolve_followup(chat_id, project_id) returns the most recent open
      pending_question_id for that project's pending_questions.json (or None
      if there are zero), plus a "multiple_open" flag when more than one
      open question exists
  (d) prune_sent_messages() removes entries whose pending_question_id is
      answered/expired, or whose posted_at is older than a configurable
      max_age_days (default 7)

All tests use tmp_path -- no real Telegram calls, no real project state.

Tests are intentionally written before the implementation so they fail on
first run (ModuleNotFoundError).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import windows.src.bot_pool_routing as routing_mod
import windows.src.project_registry as registry_mod


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# (a) record_sent_message
# ---------------------------------------------------------------------------

def test_record_sent_message_appends_entry(tmp_path):
    sent_file = tmp_path / "sent_messages.json"

    with patch.object(routing_mod, "SENT_MESSAGES_FILE", sent_file):
        entry = routing_mod.record_sent_message(
            chat_id=-1001234567890,
            message_id=4821,
            posted_by_bot="T1",
            project_id="research-agent",
            pending_question_id="dev-loop-12.1-final-review",
        )

    assert entry["chat_id"] == -1001234567890
    assert entry["message_id"] == 4821
    assert entry["posted_by_bot"] == "T1"
    assert entry["project_id"] == "research-agent"
    assert entry["pending_question_id"] == "dev-loop-12.1-final-review"
    assert "posted_at" in entry

    saved = json.loads(sent_file.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["message_id"] == 4821


def test_record_sent_message_appends_to_existing_entries(tmp_path):
    sent_file = tmp_path / "sent_messages.json"
    _write_json(sent_file, [{"chat_id": 1, "message_id": 1, "posted_by_bot": "T2",
                              "project_id": "other", "pending_question_id": "q-1",
                              "posted_at": _iso(datetime.now(timezone.utc))}])

    with patch.object(routing_mod, "SENT_MESSAGES_FILE", sent_file):
        routing_mod.record_sent_message(
            chat_id=-100999, message_id=2, posted_by_bot="T1",
            project_id="research-agent", pending_question_id="q-2",
        )

    saved = json.loads(sent_file.read_text(encoding="utf-8"))
    assert len(saved) == 2
    assert saved[1]["message_id"] == 2


# ---------------------------------------------------------------------------
# (b) resolve_reply
# ---------------------------------------------------------------------------

def test_resolve_reply_returns_matching_entry(tmp_path):
    sent_file = tmp_path / "sent_messages.json"
    _write_json(sent_file, [
        {"chat_id": -100111, "message_id": 10, "posted_by_bot": "T1",
         "project_id": "research-agent", "pending_question_id": "q-a",
         "posted_at": _iso(datetime.now(timezone.utc))},
        {"chat_id": -100111, "message_id": 11, "posted_by_bot": "T2",
         "project_id": "research-agent", "pending_question_id": "q-b",
         "posted_at": _iso(datetime.now(timezone.utc))},
    ])

    with patch.object(routing_mod, "SENT_MESSAGES_FILE", sent_file):
        entry = routing_mod.resolve_reply(-100111, 11)

    assert entry is not None
    assert entry["pending_question_id"] == "q-b"


def test_resolve_reply_returns_none_for_no_match(tmp_path):
    sent_file = tmp_path / "sent_messages.json"
    _write_json(sent_file, [
        {"chat_id": -100111, "message_id": 10, "posted_by_bot": "T1",
         "project_id": "research-agent", "pending_question_id": "q-a",
         "posted_at": _iso(datetime.now(timezone.utc))},
    ])

    with patch.object(routing_mod, "SENT_MESSAGES_FILE", sent_file):
        assert routing_mod.resolve_reply(-100111, 999) is None
        assert routing_mod.resolve_reply(-100222, 10) is None


def test_resolve_reply_returns_none_when_registry_missing(tmp_path):
    sent_file = tmp_path / "does_not_exist.json"

    with patch.object(routing_mod, "SENT_MESSAGES_FILE", sent_file):
        assert routing_mod.resolve_reply(-100111, 10) is None


# ---------------------------------------------------------------------------
# (c) resolve_followup
# ---------------------------------------------------------------------------

def _project_registry(tmp_path, project_id="research-agent", chat_id=-100111):
    state_dir = tmp_path / "proj_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    registry_file = tmp_path / "projects.json"
    _write_json(registry_file, {
        "projects": {
            project_id: {
                "chat_id": chat_id,
                "state_dir": str(state_dir),
                "project_dir": str(tmp_path),
                "dispatcher_prompt": str(tmp_path / "dispatcher_system.md"),
            }
        }
    })
    return registry_file, state_dir


def test_resolve_followup_returns_none_when_no_open_questions(tmp_path):
    registry_file, state_dir = _project_registry(tmp_path)
    _write_json(state_dir / "pending_questions.json", [
        {"id": "q-old", "status": "answered", "asked_at": _iso(datetime.now(timezone.utc))},
    ])

    with patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        assert routing_mod.resolve_followup(-100111, "research-agent") is None


def test_resolve_followup_returns_single_open_question(tmp_path):
    registry_file, state_dir = _project_registry(tmp_path)
    _write_json(state_dir / "pending_questions.json", [
        {"id": "q-old", "status": "answered", "asked_at": _iso(datetime.now(timezone.utc) - timedelta(days=1))},
        {"id": "q-open", "status": "pending", "asked_at": _iso(datetime.now(timezone.utc))},
    ])

    with patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        result = routing_mod.resolve_followup(-100111, "research-agent")

    assert result is not None
    assert result["pending_question_id"] == "q-open"
    assert result["multiple_open"] is False


def test_resolve_followup_flags_multiple_open_and_picks_most_recent(tmp_path):
    registry_file, state_dir = _project_registry(tmp_path)
    older = _iso(datetime.now(timezone.utc) - timedelta(hours=2))
    newer = _iso(datetime.now(timezone.utc))
    _write_json(state_dir / "pending_questions.json", [
        {"id": "q-older", "status": "pending", "asked_at": older},
        {"id": "q-newer", "status": "blocked_user", "asked_at": newer},
    ])

    with patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        result = routing_mod.resolve_followup(-100111, "research-agent")

    assert result is not None
    assert result["pending_question_id"] == "q-newer"
    assert result["multiple_open"] is True


def test_resolve_followup_returns_none_for_unknown_project(tmp_path):
    registry_file, _ = _project_registry(tmp_path)

    with patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        assert routing_mod.resolve_followup(-100111, "nonexistent-project") is None


# ---------------------------------------------------------------------------
# (d) prune_sent_messages
# ---------------------------------------------------------------------------

def test_prune_removes_entries_with_answered_pending_question(tmp_path):
    registry_file, state_dir = _project_registry(tmp_path)
    _write_json(state_dir / "pending_questions.json", [
        {"id": "q-answered", "status": "answered", "asked_at": _iso(datetime.now(timezone.utc))},
        {"id": "q-open", "status": "pending", "asked_at": _iso(datetime.now(timezone.utc))},
    ])

    sent_file = tmp_path / "sent_messages.json"
    now = _iso(datetime.now(timezone.utc))
    _write_json(sent_file, [
        {"chat_id": -100111, "message_id": 1, "posted_by_bot": "T1",
         "project_id": "research-agent", "pending_question_id": "q-answered", "posted_at": now},
        {"chat_id": -100111, "message_id": 2, "posted_by_bot": "T1",
         "project_id": "research-agent", "pending_question_id": "q-open", "posted_at": now},
    ])

    with patch.object(routing_mod, "SENT_MESSAGES_FILE", sent_file), \
         patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        kept = routing_mod.prune_sent_messages()

    assert len(kept) == 1
    assert kept[0]["pending_question_id"] == "q-open"

    saved = json.loads(sent_file.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["pending_question_id"] == "q-open"


def test_prune_removes_entries_older_than_max_age_days(tmp_path):
    registry_file, state_dir = _project_registry(tmp_path)
    _write_json(state_dir / "pending_questions.json", [
        {"id": "q-open", "status": "pending", "asked_at": _iso(datetime.now(timezone.utc))},
    ])

    sent_file = tmp_path / "sent_messages.json"
    old = _iso(datetime.now(timezone.utc) - timedelta(days=10))
    recent = _iso(datetime.now(timezone.utc) - timedelta(days=1))
    _write_json(sent_file, [
        {"chat_id": -100111, "message_id": 1, "posted_by_bot": "T1",
         "project_id": "research-agent", "pending_question_id": "q-open", "posted_at": old},
        {"chat_id": -100111, "message_id": 2, "posted_by_bot": "T1",
         "project_id": "research-agent", "pending_question_id": "q-open", "posted_at": recent},
    ])

    with patch.object(routing_mod, "SENT_MESSAGES_FILE", sent_file), \
         patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        kept = routing_mod.prune_sent_messages(max_age_days=7)

    assert len(kept) == 1
    assert kept[0]["message_id"] == 2


def test_prune_keeps_entries_with_unresolved_open_questions(tmp_path):
    registry_file, state_dir = _project_registry(tmp_path)
    _write_json(state_dir / "pending_questions.json", [
        {"id": "q-open", "status": "pending", "asked_at": _iso(datetime.now(timezone.utc))},
    ])

    sent_file = tmp_path / "sent_messages.json"
    recent = _iso(datetime.now(timezone.utc))
    _write_json(sent_file, [
        {"chat_id": -100111, "message_id": 1, "posted_by_bot": "T1",
         "project_id": "research-agent", "pending_question_id": "q-open", "posted_at": recent},
    ])

    with patch.object(routing_mod, "SENT_MESSAGES_FILE", sent_file), \
         patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        kept = routing_mod.prune_sent_messages()

    assert len(kept) == 1


def test_prune_returns_empty_list_when_registry_missing(tmp_path):
    sent_file = tmp_path / "does_not_exist.json"

    with patch.object(routing_mod, "SENT_MESSAGES_FILE", sent_file):
        assert routing_mod.prune_sent_messages() == []
