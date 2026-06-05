"""
Tests for dispatcher.dev_loop_lifecycle — locking the existing behaviour.

The implementation already exists in windows/src/dispatcher.py.
These tests verify it rather than driving a new implementation.

Coverage:
  (a) Clean exit: appends a new dev-loop scheduled_tasks entry 1 hour from now_berlin.
      Does NOT write budget_state.json or call send_message.
  (b) Budget exhaustion: output matches 'resets HH:MMam/pm (Europe/Berlin)'.
      Writes budget_state.json with currently_blocked=True, hit_at=now_berlin,
      window_opens_at=reset_time+10min, consecutive_hits incremented.
  (c) Budget case: resets any in_progress dev_todo task to pending.
  (d) Budget case: appends resume entry to scheduled_tasks at window_opens_at,
      calls send_message.
  (e) Crash / unclean exit (no budget pattern): calls send_message with stderr
      excerpt, does NOT append to scheduled_tasks.
  (f) hit_at is taken from the now_berlin argument, not datetime.now().

All filesystem operations are redirected to tmp_path.
"""

import asyncio
import importlib
import json
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

BERLIN = ZoneInfo("Europe/Berlin")


# ---------------------------------------------------------------------------
# Helpers to build fake modules and redirect dispatcher's file paths
# ---------------------------------------------------------------------------

def _install_dispatcher_fakes():
    """Install the minimum set of fakes so windows.src.dispatcher can be imported."""
    fake_wsl_auth = types.ModuleType("windows.src.wsl_auth")
    fake_wsl_auth.refresh_claude_auth = AsyncMock(return_value=True)
    fake_wsl_auth.diagnose_wsl_auth = AsyncMock(return_value="token ok")
    sys.modules["windows.src.wsl_auth"] = fake_wsl_auth

    fake_bot_config = types.ModuleType("windows.src.bot_config")
    fake_bot_config._cfg = MagicMock(return_value={
        "auth": {},
        "wsl_project_root": "/fake/project",
    })
    fake_bot_config.BASE = Path(__file__).parent.parent / "windows"
    sys.modules["windows.src.bot_config"] = fake_bot_config

    fake_bot_utils = types.ModuleType("windows.src.bot_utils")
    fake_bot_utils.USER_TZ = BERLIN
    sys.modules["windows.src.bot_utils"] = fake_bot_utils

    fake_state = types.ModuleType("windows.src.state")
    # These will be patched per-test via monkeypatch
    fake_state.STATE_DIR = Path("/fake/state")
    fake_state.SHARED_DIR = Path("/fake/shared")
    fake_state.compact_snapshot = MagicMock(return_value="{}")
    fake_state.append_run_log = MagicMock()
    fake_state.get_previous_session_turns = MagicMock(return_value=[])
    fake_state.read_scheduled_tasks = MagicMock(return_value=[])
    fake_state.write_scheduled_tasks = MagicMock()
    sys.modules["windows.src.state"] = fake_state

    return fake_state


def _load_dispatcher(tmp_path: Path):
    """
    Import (or re-import) windows.src.dispatcher with its STATE_DIR and
    related module-level path constants redirected to tmp_path.
    """
    _install_dispatcher_fakes()

    # Remove cached dispatcher module to force re-import with patched state
    for key in list(sys.modules):
        if "dispatcher" in key and "windows" in key:
            del sys.modules[key]

    # Create budget and todo files in tmp_path so the module finds them
    (tmp_path / "scheduled_tasks.json").write_text("[]", encoding="utf-8")
    (tmp_path / "budget_state.json").write_text(
        '{"currently_blocked": false, "hit_at": null, "window_opens_at": null, "consecutive_hits": 0}',
        encoding="utf-8",
    )

    # Patch STATE_DIR on the fake state module before dispatcher imports it
    sys.modules["windows.src.state"].STATE_DIR = tmp_path

    import windows.src.dispatcher as dispatcher

    # Redirect the module-level constants that dispatcher captured at import time
    dispatcher._BUDGET_FILE   = tmp_path / "budget_state.json"
    dispatcher._DEV_TODO_FILE = tmp_path / "dev_todo.json"

    # Redirect read/write_scheduled_tasks to use tmp_path
    def _read_tasks():
        p = tmp_path / "scheduled_tasks.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def _write_tasks(tasks):
        (tmp_path / "scheduled_tasks.json").write_text(
            json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    dispatcher.read_scheduled_tasks  = _read_tasks
    dispatcher.write_scheduled_tasks = _write_tasks

    return dispatcher


def _now_berlin():
    return datetime(2026, 6, 3, 10, 0, 0, tzinfo=BERLIN)


# ---------------------------------------------------------------------------
# (a) Clean exit: schedule next run 1 hour from now, no send_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clean_exit_schedules_next_run(tmp_path):
    disp = _load_dispatcher(tmp_path)
    send = AsyncMock()
    now = _now_berlin()

    await disp.dev_loop_lifecycle(
        output_text="All tasks complete.",
        stderr_text="",
        clean_exit=True,
        now_berlin=now,
        chat_id=42,
        send_message=send,
    )

    tasks = json.loads((tmp_path / "scheduled_tasks.json").read_text())
    assert len(tasks) == 1, "Should append exactly one scheduled task"
    task = tasks[0]
    expected_at = (now + timedelta(hours=1)).isoformat()
    assert task["scheduled_at"] == expected_at
    assert "DEV LOOP" in task["task"]
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_clean_exit_does_not_touch_budget_file(tmp_path):
    disp = _load_dispatcher(tmp_path)
    budget_before = (tmp_path / "budget_state.json").read_text()

    await disp.dev_loop_lifecycle(
        output_text="done.",
        stderr_text="",
        clean_exit=True,
        now_berlin=_now_berlin(),
        chat_id=42,
        send_message=AsyncMock(),
    )

    assert (tmp_path / "budget_state.json").read_text() == budget_before


# ---------------------------------------------------------------------------
# (b) Budget case: writes budget_state.json with correct fields
# ---------------------------------------------------------------------------

BUDGET_OUTPUT = "Your token usage resets 11:30pm (Europe/Berlin) each day."


@pytest.mark.asyncio
async def test_budget_exhaustion_writes_budget_state(tmp_path):
    disp = _load_dispatcher(tmp_path)
    now = _now_berlin()

    await disp.dev_loop_lifecycle(
        output_text=BUDGET_OUTPUT,
        stderr_text="",
        clean_exit=False,
        now_berlin=now,
        chat_id=42,
        send_message=AsyncMock(),
    )

    budget = json.loads((tmp_path / "budget_state.json").read_text())
    assert budget["currently_blocked"] is True
    assert budget["hit_at"] == now.isoformat()
    assert budget["consecutive_hits"] == 1
    # window_opens_at must be reset_time + 10 minutes
    window = datetime.fromisoformat(budget["window_opens_at"])
    assert window.hour == 23
    assert window.minute == 40  # 11:30pm + 10 min


@pytest.mark.asyncio
async def test_budget_exhaustion_increments_consecutive_hits(tmp_path):
    """consecutive_hits must be incremented from the existing value, not set to 1."""
    disp = _load_dispatcher(tmp_path)
    (tmp_path / "budget_state.json").write_text(
        json.dumps({"currently_blocked": False, "hit_at": None,
                    "window_opens_at": None, "consecutive_hits": 3}),
        encoding="utf-8",
    )

    await disp.dev_loop_lifecycle(
        output_text=BUDGET_OUTPUT,
        stderr_text="",
        clean_exit=False,
        now_berlin=_now_berlin(),
        chat_id=42,
        send_message=AsyncMock(),
    )

    budget = json.loads((tmp_path / "budget_state.json").read_text())
    assert budget["consecutive_hits"] == 4


# ---------------------------------------------------------------------------
# (c) Budget case: resets in_progress dev_todo tasks to pending
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_resets_inprogress_tasks(tmp_path):
    disp = _load_dispatcher(tmp_path)

    dev_todo = {
        "tasks": [
            {"id": "4.1", "status": "in_progress"},
            {"id": "4.2", "status": "pending"},
            {"id": "3.9", "status": "done"},
        ]
    }
    (tmp_path / "dev_todo.json").write_text(
        json.dumps(dev_todo), encoding="utf-8"
    )

    await disp.dev_loop_lifecycle(
        output_text=BUDGET_OUTPUT,
        stderr_text="",
        clean_exit=False,
        now_berlin=_now_berlin(),
        chat_id=42,
        send_message=AsyncMock(),
    )

    updated = json.loads((tmp_path / "dev_todo.json").read_text())
    by_id = {t["id"]: t for t in updated["tasks"]}
    assert by_id["4.1"]["status"] == "pending"
    assert by_id["4.2"]["status"] == "pending"
    assert by_id["3.9"]["status"] == "done"


# ---------------------------------------------------------------------------
# (d) Budget case: appends resume entry at window_opens_at, calls send_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_appends_resume_task_and_notifies(tmp_path):
    disp = _load_dispatcher(tmp_path)
    send = AsyncMock()
    now = _now_berlin()

    await disp.dev_loop_lifecycle(
        output_text=BUDGET_OUTPUT,
        stderr_text="",
        clean_exit=False,
        now_berlin=now,
        chat_id=42,
        send_message=send,
    )

    tasks = json.loads((tmp_path / "scheduled_tasks.json").read_text())
    assert len(tasks) == 1
    task = tasks[0]
    assert "resume" in task["id"]
    assert "DEV LOOP" in task["task"]
    # scheduled_at must be window_opens_at (23:40 on same or next day)
    scheduled = datetime.fromisoformat(task["scheduled_at"])
    assert scheduled.minute == 40  # 11:30pm + 10 min

    send.assert_awaited_once()
    msg_text = send.call_args[0][1]
    assert "Budget" in msg_text or "budget" in msg_text


# ---------------------------------------------------------------------------
# (e) Crash path: send_message with stderr, no scheduled task appended
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crash_notifies_and_does_not_reschedule(tmp_path):
    disp = _load_dispatcher(tmp_path)
    send = AsyncMock()

    await disp.dev_loop_lifecycle(
        output_text="",
        stderr_text="Traceback: something went wrong at line 42",
        clean_exit=False,
        now_berlin=_now_berlin(),
        chat_id=42,
        send_message=send,
    )

    tasks = json.loads((tmp_path / "scheduled_tasks.json").read_text())
    assert tasks == [], "Crash path must not append any scheduled task"
    send.assert_awaited_once()
    msg_text = send.call_args[0][1]
    assert "Traceback" in msg_text or "something went wrong" in msg_text


# ---------------------------------------------------------------------------
# (f) hit_at is taken from now_berlin argument, not system clock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hit_at_uses_now_berlin_arg_not_system_clock(tmp_path):
    """hit_at must equal the now_berlin argument passed in, regardless of system time."""
    disp = _load_dispatcher(tmp_path)

    fixed_now = datetime(2026, 1, 15, 9, 0, 0, tzinfo=BERLIN)

    await disp.dev_loop_lifecycle(
        output_text=BUDGET_OUTPUT,
        stderr_text="",
        clean_exit=False,
        now_berlin=fixed_now,
        chat_id=42,
        send_message=AsyncMock(),
    )

    budget = json.loads((tmp_path / "budget_state.json").read_text())
    assert budget["hit_at"] == fixed_now.isoformat()
