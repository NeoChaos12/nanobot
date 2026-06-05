"""
TDD tests for windows/src/state.py.

Coverage:
  (a) compact_snapshot returns a non-empty string containing expected section keys
  (b) append_run_log creates the file if missing and appends entries correctly
  (c) get_previous_session_turns returns the correct turns from the most recent session
  (d) read_scheduled_tasks / write_scheduled_tasks round-trip through tmp_path

All tests use tmp_path — no real project state files are read or written.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _evict_state_module():
    """Evict any stale fake windows.src.state injected by other test modules."""
    for key in list(sys.modules):
        if key == "windows.src.state":
            del sys.modules[key]
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_state_paths(tmp_path: Path):
    """Return a dict of patches redirecting all state.py path constants to tmp_path."""
    return {
        "windows.src.state.RUN_LOG_FILE":           tmp_path / "run_log.json",
        "windows.src.state.LESSONS_FILE":           tmp_path / "lessons.json",
        "windows.src.state.SCHEDULED_TASKS_FILE":   tmp_path / "scheduled_tasks.json",
        "windows.src.state.PENDING_QUESTIONS_FILE": tmp_path / "pending_questions.json",
        "windows.src.state.CHAT_HISTORY_FILE":      tmp_path / "chat_history.jsonl",
        "windows.src.state.AGENTS_DIR":             tmp_path / "agents",
        "windows.src.state.STATE_DIR":              tmp_path,
    }


def _apply_patches(patches: dict):
    """Context manager that applies multiple patches simultaneously."""
    import contextlib

    @contextlib.contextmanager
    def _multi():
        ctxs = [patch(k, v) for k, v in patches.items()]
        started = []
        try:
            for c in ctxs:
                started.append(c.__enter__())
            yield
        finally:
            for c, s in zip(reversed(ctxs), reversed(started)):
                c.__exit__(None, None, None)

    return _multi()


# ---------------------------------------------------------------------------
# (a) compact_snapshot
# ---------------------------------------------------------------------------

def test_compact_snapshot_returns_nonempty_string(tmp_path):
    import windows.src.state as state

    run_log = [{"timestamp": "2026-06-01T10:00:00", "target_id": "task-1", "outcome": "success"}]
    (tmp_path / "run_log.json").write_text(json.dumps(run_log))

    with _apply_patches(_patch_state_paths(tmp_path)):
        result = state.compact_snapshot()

    assert isinstance(result, str)
    assert len(result) > 0
    assert "Last run:" in result


def test_compact_snapshot_domain_fn_is_prepended(tmp_path):
    import windows.src.state as state

    run_log = [{"timestamp": "2026-06-01T10:00:00", "target_id": "task-1", "outcome": "success"}]
    (tmp_path / "run_log.json").write_text(json.dumps(run_log))

    def my_domain():
        return "Targets: 3 total (2 pending, 1 done)"

    with _apply_patches(_patch_state_paths(tmp_path)):
        result = state.compact_snapshot(domain_fn=my_domain)

    assert "Targets: 3 total" in result
    assert result.startswith("Targets:")
    assert "Last run:" in result


# ---------------------------------------------------------------------------
# (b) append_run_log
# ---------------------------------------------------------------------------

def test_append_run_log_creates_file_if_missing(tmp_path):
    import windows.src.state as state

    log_file = tmp_path / "run_log.json"
    assert not log_file.exists()

    entry = {"timestamp": "2026-06-01T10:00:00", "target_id": "test", "outcome": "success"}

    with _apply_patches(_patch_state_paths(tmp_path)):
        state.append_run_log(entry)

    assert log_file.exists()
    data = json.loads(log_file.read_text())
    assert len(data) == 1
    assert data[0]["target_id"] == "test"


def test_append_run_log_appends_to_existing(tmp_path):
    import windows.src.state as state

    log_file = tmp_path / "run_log.json"
    log_file.write_text(json.dumps([{"target_id": "first", "outcome": "success"}]))

    with _apply_patches(_patch_state_paths(tmp_path)):
        state.append_run_log({"target_id": "second", "outcome": "failure"})

    data = json.loads(log_file.read_text())
    assert len(data) == 2
    assert data[0]["target_id"] == "first"
    assert data[1]["target_id"] == "second"


# ---------------------------------------------------------------------------
# (c) get_previous_session_turns
# ---------------------------------------------------------------------------

def test_get_previous_session_turns_returns_last_session(tmp_path):
    import windows.src.state as state

    history_file = tmp_path / "chat_history.jsonl"
    turns = [
        {"chat_id": 1, "session_id": "sess-A", "role": "user",      "text": "hello A"},
        {"chat_id": 1, "session_id": "sess-A", "role": "assistant",  "text": "hi A"},
        {"chat_id": 1, "session_id": "sess-B", "role": "user",      "text": "hello B"},
        {"chat_id": 1, "session_id": "sess-B", "role": "assistant",  "text": "hi B"},
    ]
    history_file.write_text("\n".join(json.dumps(t) for t in turns) + "\n")

    with _apply_patches(_patch_state_paths(tmp_path)):
        result = state.get_previous_session_turns(chat_id=1, n=10)

    assert len(result) == 2
    assert all(t["session_id"] == "sess-B" for t in result)
    assert result[0]["text"] == "hello B"
    assert result[1]["text"] == "hi B"


def test_get_previous_session_turns_respects_n_limit(tmp_path):
    import windows.src.state as state

    history_file = tmp_path / "chat_history.jsonl"
    turns = [
        {"chat_id": 1, "session_id": "sess-X", "role": "user",     "text": f"msg {i}"}
        for i in range(5)
    ]
    history_file.write_text("\n".join(json.dumps(t) for t in turns) + "\n")

    with _apply_patches(_patch_state_paths(tmp_path)):
        result = state.get_previous_session_turns(chat_id=1, n=2)

    assert len(result) == 2
    assert result[0]["text"] == "msg 3"
    assert result[1]["text"] == "msg 4"


def test_get_previous_session_turns_empty_if_no_file(tmp_path):
    import windows.src.state as state

    with _apply_patches(_patch_state_paths(tmp_path)):
        result = state.get_previous_session_turns(chat_id=1, n=10)

    assert result == []


def test_get_previous_session_turns_filters_by_chat_id(tmp_path):
    import windows.src.state as state

    history_file = tmp_path / "chat_history.jsonl"
    turns = [
        {"chat_id": 1, "session_id": "s1", "role": "user", "text": "chat1"},
        {"chat_id": 2, "session_id": "s2", "role": "user", "text": "chat2"},
    ]
    history_file.write_text("\n".join(json.dumps(t) for t in turns) + "\n")

    with _apply_patches(_patch_state_paths(tmp_path)):
        result = state.get_previous_session_turns(chat_id=1, n=10)

    assert len(result) == 1
    assert result[0]["text"] == "chat1"


# ---------------------------------------------------------------------------
# (d) read / write_scheduled_tasks round-trip
# ---------------------------------------------------------------------------

def test_read_write_scheduled_tasks_roundtrip(tmp_path):
    import windows.src.state as state

    tasks = [
        {"id": "task-1", "status": "pending", "scheduled_at": "2026-06-02T10:00:00+02:00"},
        {"id": "task-2", "status": "done",    "scheduled_at": "2026-06-01T08:00:00+02:00"},
    ]

    with _apply_patches(_patch_state_paths(tmp_path)):
        state.write_scheduled_tasks(tasks)
        result = state.read_scheduled_tasks()

    assert result == tasks


def test_read_scheduled_tasks_returns_empty_list_if_missing(tmp_path):
    import windows.src.state as state

    with _apply_patches(_patch_state_paths(tmp_path)):
        result = state.read_scheduled_tasks()

    assert result == []


def test_write_scheduled_tasks_is_atomic(tmp_path):
    """write_scheduled_tasks should not leave a .tmp file on success."""
    import windows.src.state as state

    with _apply_patches(_patch_state_paths(tmp_path)):
        state.write_scheduled_tasks([{"id": "t1", "status": "pending"}])

    tmp_file = tmp_path / "scheduled_tasks.tmp"
    assert not tmp_file.exists()
    assert (tmp_path / "scheduled_tasks.json").exists()


# ---------------------------------------------------------------------------
# (e) append_chat_turn — HTML escaping
# ---------------------------------------------------------------------------

def test_append_chat_turn_html_escapes_angle_brackets(tmp_path):
    """Text containing XML tags is stored with < and > HTML-escaped."""
    import windows.src.state as state

    with _apply_patches(_patch_state_paths(tmp_path)):
        state.append_chat_turn(1, "s1", "user", '<turn role="user">hello</turn>')

    line = json.loads((tmp_path / "chat_history.jsonl").read_text().strip())
    assert "&lt;" in line["text"]
    assert "&gt;" in line["text"]
    assert "<turn" not in line["text"]


def test_append_chat_turn_html_escapes_ampersands(tmp_path):
    """Ampersands in text are stored as &amp;."""
    import windows.src.state as state

    with _apply_patches(_patch_state_paths(tmp_path)):
        state.append_chat_turn(1, "s1", "user", "a & b")

    line = json.loads((tmp_path / "chat_history.jsonl").read_text().strip())
    assert "&amp;" in line["text"]
    assert " & " not in line["text"]


def test_append_chat_turn_plain_text_unchanged(tmp_path):
    """Plain text with no special characters is stored verbatim."""
    import windows.src.state as state

    with _apply_patches(_patch_state_paths(tmp_path)):
        state.append_chat_turn(1, "s1", "assistant", "hello world")

    line = json.loads((tmp_path / "chat_history.jsonl").read_text().strip())
    assert line["text"] == "hello world"
