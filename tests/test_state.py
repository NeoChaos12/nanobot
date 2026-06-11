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


# ---------------------------------------------------------------------------
# Chat-history rotation & archival helpers
# ---------------------------------------------------------------------------
#
# These tests cover constants/functions not yet present in framework-repo's
# state.py (CONFIG_FILE, CHAT_HISTORY_ARCHIVE_DIR, _chat_history_settings,
# _rotate_chat_history_if_stale, _prune_chat_history_archive, plus the
# rotation/pruning hooks in append_chat_turn and the archive fallback in
# get_previous_session_turns). Patched with create=True so they fail with a
# clear assertion/AttributeError before the corresponding implementation
# (task 8.2) lands, without disturbing the patch dict used by tests above.

def _patch_chat_history_paths(tmp_path: Path):
    patches = _patch_state_paths(tmp_path)
    patches["windows.src.state.CHAT_HISTORY_ARCHIVE_DIR"] = tmp_path / "chat_history_archive"
    patches["windows.src.state.CONFIG_FILE"] = tmp_path / "nanobot.config.json"
    return patches


def _apply_patches_create(patches: dict):
    """Like _apply_patches, but allows patching attributes that don't exist yet (create=True)."""
    import contextlib

    @contextlib.contextmanager
    def _multi():
        ctxs = [patch(k, v, create=True) for k, v in patches.items()]
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
# (f) _chat_history_settings
# ---------------------------------------------------------------------------

def test_chat_history_settings_defaults_when_no_config(tmp_path):
    import windows.src.state as state

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        settings = state._chat_history_settings()

    assert settings == {
        "idle_timeout_seconds": 1800,
        "chat_history_rotate_multiplier": 3,
        "chat_history_max_size_mb": 5,
    }


def test_chat_history_settings_config_override(tmp_path):
    import windows.src.state as state

    config_file = tmp_path / "nanobot.config.json"
    config_file.write_text(json.dumps({
        "session": {
            "idle_timeout_seconds": 60,
            "chat_history_rotate_multiplier": 2,
            "chat_history_max_size_mb": 1,
        }
    }))

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        settings = state._chat_history_settings()

    assert settings == {
        "idle_timeout_seconds": 60,
        "chat_history_rotate_multiplier": 2,
        "chat_history_max_size_mb": 1,
    }


def test_chat_history_settings_partial_override_uses_defaults_for_rest(tmp_path):
    import windows.src.state as state

    config_file = tmp_path / "nanobot.config.json"
    config_file.write_text(json.dumps({"session": {"chat_history_max_size_mb": 10}}))

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        settings = state._chat_history_settings()

    assert settings["chat_history_max_size_mb"] == 10
    assert settings["idle_timeout_seconds"] == 1800
    assert settings["chat_history_rotate_multiplier"] == 3


# ---------------------------------------------------------------------------
# (g) _rotate_chat_history_if_stale
# ---------------------------------------------------------------------------

def test_rotate_chat_history_noop_if_file_missing(tmp_path):
    import windows.src.state as state

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        state._rotate_chat_history_if_stale()

    assert not (tmp_path / "chat_history.jsonl").exists()
    assert not (tmp_path / "chat_history_archive").exists()


def test_rotate_chat_history_noop_if_file_empty(tmp_path):
    import windows.src.state as state

    history_file = tmp_path / "chat_history.jsonl"
    history_file.write_text("")

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        state._rotate_chat_history_if_stale()

    assert history_file.exists()
    assert not (tmp_path / "chat_history_archive").exists()


def test_rotate_chat_history_noop_when_fresh(tmp_path):
    import windows.src.state as state
    from datetime import datetime, timezone

    history_file = tmp_path / "chat_history.jsonl"
    fresh_ts = datetime.now(timezone.utc).isoformat()
    history_file.write_text(json.dumps({
        "chat_id": 1, "session_id": "s1", "role": "user", "text": "hi", "timestamp": fresh_ts,
    }) + "\n")

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        state._rotate_chat_history_if_stale()

    assert history_file.exists()
    assert not (tmp_path / "chat_history_archive").exists()


def test_rotate_chat_history_rotates_when_stale(tmp_path):
    import windows.src.state as state
    from datetime import datetime, timezone, timedelta

    history_file = tmp_path / "chat_history.jsonl"
    old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    history_file.write_text(json.dumps({
        "chat_id": 1, "session_id": "s1", "role": "user", "text": "hi", "timestamp": old_ts.isoformat(),
    }) + "\n")

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        state._rotate_chat_history_if_stale()

    archive_dir = tmp_path / "chat_history_archive"
    assert not history_file.exists()
    assert archive_dir.exists()
    archives = list(archive_dir.glob("chat_history_*.jsonl"))
    assert len(archives) == 1


# ---------------------------------------------------------------------------
# (h) _prune_chat_history_archive
# ---------------------------------------------------------------------------

def test_prune_chat_history_archive_deletes_oldest_first(tmp_path):
    import windows.src.state as state

    archive_dir = tmp_path / "chat_history_archive"
    archive_dir.mkdir()
    for name in [
        "chat_history_20260101T000000Z.jsonl",
        "chat_history_20260102T000000Z.jsonl",
        "chat_history_20260103T000000Z.jsonl",
    ]:
        (archive_dir / name).write_text("x" * 40)

    config_file = tmp_path / "nanobot.config.json"
    config_file.write_text(json.dumps({"session": {"chat_history_max_size_mb": 0.0001}}))

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        state._prune_chat_history_archive()
        remaining = sorted(p.name for p in archive_dir.glob("*.jsonl"))

    # cap ~105 bytes; 3*40=120 > cap, oldest deleted first until <= cap
    assert "chat_history_20260101T000000Z.jsonl" not in remaining
    assert "chat_history_20260103T000000Z.jsonl" in remaining


def test_prune_chat_history_archive_keeps_active_file(tmp_path):
    import windows.src.state as state

    history_file = tmp_path / "chat_history.jsonl"
    history_file.write_text("x" * 1000)

    config_file = tmp_path / "nanobot.config.json"
    config_file.write_text(json.dumps({"session": {"chat_history_max_size_mb": 0.0001}}))

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        state._prune_chat_history_archive()

    assert history_file.exists()


def test_prune_chat_history_archive_noop_if_under_cap(tmp_path):
    import windows.src.state as state

    archive_dir = tmp_path / "chat_history_archive"
    archive_dir.mkdir()
    (archive_dir / "chat_history_20260101T000000Z.jsonl").write_text("x" * 10)

    config_file = tmp_path / "nanobot.config.json"
    config_file.write_text(json.dumps({"session": {"chat_history_max_size_mb": 5}}))

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        state._prune_chat_history_archive()

    assert (archive_dir / "chat_history_20260101T000000Z.jsonl").exists()


# ---------------------------------------------------------------------------
# (i) append_chat_turn — rotation & pruning integration
# ---------------------------------------------------------------------------

def test_append_chat_turn_rotates_stale_history_before_appending(tmp_path):
    import windows.src.state as state
    from datetime import datetime, timezone, timedelta

    history_file = tmp_path / "chat_history.jsonl"
    old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    history_file.write_text(json.dumps({
        "chat_id": 1, "session_id": "old", "role": "user", "text": "old msg", "timestamp": old_ts.isoformat(),
    }) + "\n")

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        state.append_chat_turn(1, "new", "user", "new msg")

    archive_dir = tmp_path / "chat_history_archive"
    assert archive_dir.exists()
    assert len(list(archive_dir.glob("chat_history_*.jsonl"))) == 1

    lines = history_file.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["session_id"] == "new"
    assert entry["text"] == "new msg"


def test_append_chat_turn_prunes_archive_after_appending(tmp_path):
    import windows.src.state as state

    archive_dir = tmp_path / "chat_history_archive"
    archive_dir.mkdir()
    for name in [
        "chat_history_20260101T000000Z.jsonl",
        "chat_history_20260102T000000Z.jsonl",
        "chat_history_20260103T000000Z.jsonl",
    ]:
        (archive_dir / name).write_text("x" * 60)

    config_file = tmp_path / "nanobot.config.json"
    config_file.write_text(json.dumps({"session": {"chat_history_max_size_mb": 0.0002}}))

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        state.append_chat_turn(1, "s1", "user", "hi")
        remaining = sorted(p.name for p in archive_dir.glob("*.jsonl"))

    assert "chat_history_20260101T000000Z.jsonl" not in remaining
    assert "chat_history_20260103T000000Z.jsonl" in remaining


# ---------------------------------------------------------------------------
# (j) get_previous_session_turns — archive fallback
# ---------------------------------------------------------------------------

def test_get_previous_session_turns_falls_back_to_latest_archive(tmp_path):
    import windows.src.state as state

    archive_dir = tmp_path / "chat_history_archive"
    archive_dir.mkdir()

    older_turns = [
        {"chat_id": 1, "session_id": "sess-old", "role": "user", "text": "older A"},
        {"chat_id": 1, "session_id": "sess-old", "role": "assistant", "text": "older B"},
    ]
    newer_turns = [
        {"chat_id": 1, "session_id": "sess-recent", "role": "user", "text": "recent A"},
        {"chat_id": 1, "session_id": "sess-recent", "role": "assistant", "text": "recent B"},
    ]
    (archive_dir / "chat_history_20260101T000000Z.jsonl").write_text(
        "\n".join(json.dumps(t) for t in older_turns) + "\n"
    )
    (archive_dir / "chat_history_20260102T000000Z.jsonl").write_text(
        "\n".join(json.dumps(t) for t in newer_turns) + "\n"
    )

    # active chat_history.jsonl is missing entirely
    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        result = state.get_previous_session_turns(chat_id=1, n=10)

    assert len(result) == 2
    assert all(t["session_id"] == "sess-recent" for t in result)
    assert result[0]["text"] == "recent A"


def test_get_previous_session_turns_prefers_active_file_over_archive(tmp_path):
    import windows.src.state as state

    history_file = tmp_path / "chat_history.jsonl"
    history_file.write_text(json.dumps({
        "chat_id": 1, "session_id": "active", "role": "user", "text": "active turn",
    }) + "\n")

    archive_dir = tmp_path / "chat_history_archive"
    archive_dir.mkdir()
    (archive_dir / "chat_history_20260101T000000Z.jsonl").write_text(json.dumps({
        "chat_id": 1, "session_id": "archived", "role": "user", "text": "archived turn",
    }) + "\n")

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        result = state.get_previous_session_turns(chat_id=1, n=10)

    assert len(result) == 1
    assert result[0]["text"] == "active turn"


def test_get_previous_session_turns_no_fallback_for_other_chat(tmp_path):
    import windows.src.state as state

    archive_dir = tmp_path / "chat_history_archive"
    archive_dir.mkdir()
    (archive_dir / "chat_history_20260101T000000Z.jsonl").write_text(json.dumps({
        "chat_id": 2, "session_id": "s1", "role": "user", "text": "other chat",
    }) + "\n")

    with _apply_patches_create(_patch_chat_history_paths(tmp_path)):
        result = state.get_previous_session_turns(chat_id=1, n=10)

    assert result == []
