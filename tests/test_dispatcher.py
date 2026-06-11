"""
TDD tests for windows/src/dispatcher.py.

Covers:
  (a) _parse_output extracts result text and session_id from valid JSONL stream
  (b) _parse_output falls back gracefully on malformed output
  (c) run_dispatcher with mocked subprocess returns expected dict shape
  (d) timeout path returns error dict with session_id=None

All subprocess and auth calls are mocked.
"""

import asyncio
import json
import sys
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------

def _import_module():
    """Import (or re-import) windows.src.dispatcher."""
    mod_name = "windows.src.dispatcher"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    # Evict stale sub-dependencies, then re-inject a minimal bot_utils fake that
    # satisfies the USER_TZ import without needing the tzlocal package at test time.
    for name in list(sys.modules):
        if name in ("windows.src.state", "windows.src.wsl_auth", "windows.src.bot_config",
                    "windows.src.bot_utils"):
            del sys.modules[name]
    fake_bot_utils = types.ModuleType("windows.src.bot_utils")
    fake_bot_utils.USER_TZ = ZoneInfo("Europe/Berlin")
    fake_bot_utils._is_allowed = lambda chat_id: True
    fake_bot_utils._send = AsyncMock()
    sys.modules["windows.src.bot_utils"] = fake_bot_utils
    import windows.src.dispatcher as m
    return m


# ---------------------------------------------------------------------------
# (a) _parse_output — valid JSONL stream
# ---------------------------------------------------------------------------

def test_parse_output_extracts_result_and_session_id():
    """Last type=result object is returned; result text and session_id extracted."""
    mod = _import_module()

    jsonl = "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking..."}]}}),
        json.dumps({"type": "result", "subtype": "success", "result": "Hello from Claude", "session_id": "sess-abc123", "cost_usd": 0.001}),
    ])

    out = mod._parse_output(jsonl)

    assert out.get("result") == "Hello from Claude"
    assert out.get("session_id") == "sess-abc123"


def test_parse_output_picks_last_result_object():
    """If multiple type=result objects appear, the last one wins."""
    mod = _import_module()

    jsonl = "\n".join([
        json.dumps({"type": "result", "result": "first", "session_id": "sess-1"}),
        json.dumps({"type": "result", "result": "last",  "session_id": "sess-2"}),
    ])

    out = mod._parse_output(jsonl)
    assert out.get("result") == "last"
    assert out.get("session_id") == "sess-2"


def test_parse_output_single_result_object():
    """A single-line JSON object (not JSONL) also works."""
    mod = _import_module()

    single = json.dumps({"type": "result", "result": "ok", "session_id": "sess-solo"})
    out = mod._parse_output(single)
    assert out.get("result") == "ok"


# ---------------------------------------------------------------------------
# (b) _parse_output — malformed / fallback
# ---------------------------------------------------------------------------

def test_parse_output_fallback_on_completely_invalid():
    """Completely non-JSON input returns a dict with raw text and session_id=None."""
    mod = _import_module()

    out = mod._parse_output("this is not json at all")
    assert isinstance(out, dict)
    assert out.get("session_id") is None
    assert "this is not json at all" in out.get("result", "")


def test_parse_output_fallback_on_mixed_valid_invalid():
    """Malformed lines are skipped; the last valid type=result wins."""
    mod = _import_module()

    jsonl = "\n".join([
        "not json",
        json.dumps({"type": "result", "result": "good", "session_id": "sess-ok"}),
        "still not json",
    ])

    out = mod._parse_output(jsonl)
    assert out.get("result") == "good"
    assert out.get("session_id") == "sess-ok"


def test_parse_output_empty_string():
    """Empty input returns a dict (no crash)."""
    mod = _import_module()
    out = mod._parse_output("")
    assert isinstance(out, dict)


def test_parse_output_no_result_type_falls_back_to_single_parse():
    """JSONL with no type=result object falls back to single-object parse."""
    mod = _import_module()

    # Valid JSON but not a JSONL stream with type=result
    single = json.dumps({"something": "else", "session_id": None})
    out = mod._parse_output(single)
    assert isinstance(out, dict)
    assert out.get("something") == "else"


# ---------------------------------------------------------------------------
# (c) run_dispatcher — happy path, mocked subprocess
# ---------------------------------------------------------------------------

def _make_process_mock(stdout: bytes, stderr: bytes, returncode: int = 0):
    """Build an AsyncMock that mimics asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_run_dispatcher_returns_expected_shape():
    """run_dispatcher returns a dict with text, session_id, cost_usd, error keys."""
    mod = _import_module()

    result_obj = {
        "type": "result",
        "result": "Test response",
        "session_id": "sess-xyz",
        "cost_usd": 0.002,
    }
    stdout = (json.dumps(result_obj) + "\n").encode()
    stderr = b""

    proc_mock = _make_process_mock(stdout, stderr, returncode=0)

    with patch("windows.src.dispatcher.refresh_claude_auth", new=AsyncMock(return_value=True)), \
         patch("windows.src.dispatcher._sync_wsl_clock", new=AsyncMock()), \
         patch("windows.src.dispatcher._wsl_project_root", return_value="/mnt/fake/project"), \
         patch("windows.src.dispatcher._build_first_turn", return_value="system prompt + hello world"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc_mock)), \
         patch("windows.src.dispatcher.append_run_log"):

        result = await mod.run_dispatcher("hello world", chat_id=111)

    assert "text" in result
    assert "session_id" in result
    assert "cost_usd" in result
    assert "error" in result
    assert result["text"] == "Test response"
    assert result["session_id"] == "sess-xyz"
    assert result["error"] is False


@pytest.mark.asyncio
async def test_run_dispatcher_propagates_session_id():
    """When a session_id is passed, it is used on resume and returned."""
    mod = _import_module()

    result_obj = {
        "type": "result",
        "result": "Resumed response",
        "session_id": "sess-resume",
        "cost_usd": 0.001,
    }
    stdout = (json.dumps(result_obj) + "\n").encode()

    proc_mock = _make_process_mock(stdout, b"", returncode=0)

    with patch("windows.src.dispatcher.refresh_claude_auth", new=AsyncMock(return_value=True)), \
         patch("windows.src.dispatcher._sync_wsl_clock", new=AsyncMock()), \
         patch("windows.src.dispatcher._wsl_project_root", return_value="/mnt/fake/project"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc_mock)), \
         patch("windows.src.dispatcher.append_run_log"):

        result = await mod.run_dispatcher("follow-up", session_id="sess-resume", chat_id=111)

    assert result["session_id"] == "sess-resume"
    assert result["text"] == "Resumed response"


@pytest.mark.asyncio
async def test_run_dispatcher_auth_failure_returns_error():
    """When auth refresh fails, run_dispatcher returns an error dict without spawning bwrap."""
    mod = _import_module()

    with patch("windows.src.dispatcher.refresh_claude_auth", new=AsyncMock(return_value=False)), \
         patch("windows.src.dispatcher._sync_wsl_clock", new=AsyncMock()), \
         patch("windows.src.dispatcher.diagnose_wsl_auth", new=AsyncMock(return_value="no credentials")), \
         patch("asyncio.create_subprocess_exec") as mock_spawn:

        result = await mod.run_dispatcher("test", chat_id=111)

    mock_spawn.assert_not_called()
    assert result.get("error") is True
    assert result.get("auth_error") is True
    assert result.get("session_id") is None


# ---------------------------------------------------------------------------
# (d) timeout path — session_id=None
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# (e) _build_first_turn — history block format
# ---------------------------------------------------------------------------

def _build_first_turn_patches(turns=None, template="SYS {STATE_SNAPSHOT} {IDLE_TIMEOUT_SECONDS} {USER_TIMEZONE}"):
    """Return a dict of patches for _build_first_turn dependencies."""
    from unittest.mock import MagicMock, patch
    fake_path = MagicMock()
    fake_path.read_text.return_value = template
    return {
        "windows.src.dispatcher.SYSTEM_PROMPT_PATH": fake_path,
        "windows.src.dispatcher.compact_snapshot": MagicMock(return_value="snap"),
        "windows.src.dispatcher._idle_timeout": MagicMock(return_value=600),
        "windows.src.dispatcher._user_timezone_label": MagicMock(return_value="Europe/Berlin"),
        "windows.src.dispatcher.get_previous_session_turns": MagicMock(return_value=turns or []),
    }


def _apply_bft_patches(patches: dict):
    """Apply multiple patches as a context manager."""
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


def test_build_first_turn_no_history():
    """No turns → no <history> block in output."""
    mod = _import_module()
    patches = _build_first_turn_patches(turns=[])

    with _apply_bft_patches(patches):
        result = mod._build_first_turn("hello", chat_id=1)

    assert "<history>" not in result
    assert "hello" in result
    assert "---" in result


def test_build_first_turn_wraps_turns_in_xml():
    """Turns from previous session are wrapped in <history><turn role="..."> XML."""
    mod = _import_module()
    turns = [
        {"role": "user",      "text": "hello"},
        {"role": "assistant", "text": "hi there"},
    ]
    patches = _build_first_turn_patches(turns=turns)

    with _apply_bft_patches(patches):
        result = mod._build_first_turn("new message", chat_id=1)

    assert "<history>" in result
    assert "</history>" in result
    assert '<turn role="user">hello</turn>' in result
    assert '<turn role="assistant">hi there</turn>' in result


def test_build_first_turn_escapes_newlines_in_turns():
    """Newlines inside turn text become literal \\n so the XML stays single-line."""
    import re
    mod = _import_module()
    turns = [{"role": "user", "text": "line1\nline2"}]
    patches = _build_first_turn_patches(turns=turns)

    with _apply_bft_patches(patches):
        result = mod._build_first_turn("msg", chat_id=1)

    assert r"line1\nline2" in result
    m = re.search(r'<turn role="user">(.*?)</turn>', result)
    assert m is not None
    assert "\n" not in m.group(1)


def test_build_first_turn_escapes_backslashes_in_turns():
    """Backslashes inside turn text are doubled so they survive later unescaping."""
    mod = _import_module()
    turns = [{"role": "user", "text": r"path\to\file"}]
    patches = _build_first_turn_patches(turns=turns)

    with _apply_bft_patches(patches):
        result = mod._build_first_turn("msg", chat_id=1)

    assert r"path\\to\\file" in result


def test_build_first_turn_none_chat_id_skips_history():
    """chat_id=None → get_previous_session_turns not called, no <history> block."""
    mod = _import_module()
    patches = _build_first_turn_patches()

    with _apply_bft_patches(patches):
        result = mod._build_first_turn("msg", chat_id=None)

    patches["windows.src.dispatcher.get_previous_session_turns"].assert_not_called()


def test_build_first_turn_history_turns_override():
    """An explicit history_turns overrides the configured chat_history_turns default."""
    mod = _import_module()
    patches = _build_first_turn_patches(turns=[])
    patches["windows.src.dispatcher._chat_history_turns"] = MagicMock(return_value=3)

    with _apply_bft_patches(patches):
        mod._build_first_turn("msg", chat_id=1, history_turns=7)

    patches["windows.src.dispatcher.get_previous_session_turns"].assert_called_once_with(1, 7)
    patches["windows.src.dispatcher._chat_history_turns"].assert_not_called()


def test_build_first_turn_no_override_uses_configured_default():
    """When history_turns is None, the configured chat_history_turns default is used."""
    mod = _import_module()
    patches = _build_first_turn_patches(turns=[])
    patches["windows.src.dispatcher._chat_history_turns"] = MagicMock(return_value=3)

    with _apply_bft_patches(patches):
        mod._build_first_turn("msg", chat_id=1)

    patches["windows.src.dispatcher.get_previous_session_turns"].assert_called_once_with(1, 3)


# ---------------------------------------------------------------------------
# (d) timeout path — session_id=None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_dispatcher_timeout_returns_error_with_no_session_id():
    """When the subprocess times out, the returned dict has error=True, session_id=None."""
    mod = _import_module()

    proc_mock = MagicMock()
    proc_mock.pid = 99999
    proc_mock.returncode = None
    proc_mock.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    proc_mock.kill = MagicMock()

    with patch("windows.src.dispatcher.refresh_claude_auth", new=AsyncMock(return_value=True)), \
         patch("windows.src.dispatcher._sync_wsl_clock", new=AsyncMock()), \
         patch("windows.src.dispatcher._wsl_project_root", return_value="/mnt/fake/project"), \
         patch("windows.src.dispatcher._build_first_turn", return_value="system prompt + slow message"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc_mock)), \
         patch("windows.src.dispatcher.append_run_log"), \
         patch("windows.src.dispatcher._dispatch_timeout", return_value=1):

        result = await mod.run_dispatcher("slow message", chat_id=111)

    assert result.get("error") is True
    assert result.get("session_id") is None
    assert "timeout" in result.get("text", "").lower() or "timed out" in result.get("text", "").lower()
