"""
dispatcher.py — Claude Code subprocess wrapper and session lifecycle manager.

Each call spawns a bubblewrap-sandboxed WSL process:
    bwrap --ro-bind / / [writable overrides] -- bash -l -c "claude -p ..."

Sandbox policy:
  - Entire filesystem mounted read-only
  - Write access granted only to: project root, ~/.claude (session files), /tmp
  - Network access unrestricted (needed for web research)

Flags:
  --dangerously-skip-permissions  suppress Claude Code's interactive approval prompts
  --output-format json            JSONL stream; final type=result carries text + session_id

New session:  system prompt + state snapshot prepended to the first user message.
Resume:       --resume <session_id>; only the user message is sent via stdin.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

from windows.src.state import compact_snapshot, append_run_log, SHARED_DIR, STATE_DIR, get_previous_session_turns, read_scheduled_tasks, write_scheduled_tasks
from windows.src.wsl_auth import refresh_claude_auth, diagnose_wsl_auth
from windows.src.bot_utils import USER_TZ

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_PATH = SHARED_DIR / "prompts" / "dispatcher_system.md"
_CONFIG_PATH       = SHARED_DIR / "config" / "nanobot.config.json"


def _wsl_project_root() -> str:
    """
    Return the WSL path to the project root.

    Resolution order:
      1. NANOBOT_PROJECT_ROOT environment variable
      2. 'wsl_project_root' key in nanobot.config.json
      3. Raises RuntimeError — no safe default exists.
    """
    env_val = os.environ.get("NANOBOT_PROJECT_ROOT", "").strip()
    if env_val:
        return env_val

    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        cfg_val = cfg.get("wsl_project_root", "").strip()
        if cfg_val:
            return cfg_val
    except Exception:
        pass

    raise RuntimeError(
        "WSL project root is not configured. "
        "Set NANOBOT_PROJECT_ROOT env var or add 'wsl_project_root' to nanobot.config.json."
    )


def _chat_history_turns() -> int:
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return int(cfg.get("session", {}).get("chat_history_turns", 3))
    except Exception:
        return 3


def _idle_timeout() -> int:
    """Read idle_timeout_seconds from config; default 600."""
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return int(cfg.get("session", {}).get("idle_timeout_seconds", 600))
    except Exception:
        return 600


def _dispatch_timeout() -> int:
    """Read dispatch_timeout_seconds from config; default 600."""
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return int(cfg.get("session", {}).get("dispatch_timeout_seconds", 600))
    except Exception:
        return 600


def _auth_config() -> dict:
    """Read auth settings from config; return defaults if missing."""
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg.get("auth", {})
    except Exception:
        return {}


# Global active-jobs registry — keyed by Windows PID of the wsl.exe process.
# Shared with interrupt.py for signal delivery.
active_jobs: dict[int, dict] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_first_turn(user_message: str, chat_id: Optional[int] = None) -> str:
    """
    Construct the full input for a new session:
    system prompt (with state snapshot injected), optional recent history from the
    previous session, followed by the user message.
    """
    template = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    snapshot = compact_snapshot()
    system = template.replace("{STATE_SNAPSHOT}", snapshot)
    system = system.replace("{IDLE_TIMEOUT_SECONDS}", str(_idle_timeout()))
    system = system.replace("{USER_TIMEZONE}", _user_timezone_label())

    history_block = ""
    if chat_id is not None:
        turns = get_previous_session_turns(chat_id, _chat_history_turns())
        if turns:
            lines = []
            for t in turns:
                role = t["role"]  # "user" or "assistant" — structural, not parsed from text
                safe_text = t["text"].replace("\\", "\\\\").replace("\n", "\\n")
                lines.append(f'<turn role="{role}">{safe_text}</turn>')
            history_block = (
                "\n\n## Recent conversation (previous session)\n\n"
                "<history>\n"
                + "\n".join(lines)
                + "\n</history>"
            )

    return f"{system}{history_block}\n\n---\n\nUser: {user_message}"


def _parse_output(raw: str) -> dict:
    """
    Claude Code --output-format json emits a stream of newline-delimited JSON objects.
    Find the last object whose type == "result"; that carries the text and session_id.
    Falls back gracefully if parsing fails.
    """
    result_obj: Optional[dict] = None

    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("type") == "result":
                result_obj = obj
        except json.JSONDecodeError:
            continue

    if result_obj is not None:
        return result_obj

    # Single-object fallback
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    # Last resort — return raw text so the caller always gets something
    logger.warning("Could not parse Claude output as JSON; returning raw text.")
    return {"result": raw.strip(), "session_id": None}


# ---------------------------------------------------------------------------
# Dev loop lifecycle
# ---------------------------------------------------------------------------

_DEV_TODO_FILE   = STATE_DIR / "dev_todo.json"
_BUDGET_FILE     = STATE_DIR / "budget_state.json"


def _user_timezone_label() -> str:
    """Return 'Timezone/Name (currently UTC±H, ABBR)' for injection into system prompts."""
    now_local = datetime.now(USER_TZ)
    offset_h = int(now_local.utcoffset().total_seconds() // 3600)
    abbr = now_local.strftime("%Z")
    sign = "+" if offset_h >= 0 else ""
    key = getattr(USER_TZ, "key", abbr)
    return f"{key} (currently UTC{sign}{offset_h}, {abbr})"


_BUDGET_RESET_RE = re.compile(r"resets\s+(\d{1,2}:\d{2}(?:am|pm))\s+\([^)]+\)", re.IGNORECASE)


def _reset_inprogress_tasks() -> None:
    """Unconditionally reset any in_progress dev_todo tasks to pending."""
    if not _DEV_TODO_FILE.exists():
        return
    try:
        data = json.loads(_DEV_TODO_FILE.read_text(encoding="utf-8"))
        changed = False
        for task in data.get("tasks", []):
            if task.get("status") == "in_progress":
                task["status"] = "pending"
                changed = True
        if changed:
            _DEV_TODO_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to reset in_progress tasks: %s", exc)


def _parse_budget_reset(text: str) -> Optional[datetime]:
    """
    Extract the budget reset time from Claude's error message, e.g.
    'resets 9:30pm (Europe/Berlin)'. Returns a Berlin-aware datetime for
    today (or tomorrow if the time has already passed), or None if not found.
    """
    m = _BUDGET_RESET_RE.search(text)
    if not m:
        return None
    try:
        now_local = datetime.now(USER_TZ)
        reset_naive = datetime.strptime(m.group(1).lower(), "%I:%M%p")
        reset_local = now_local.replace(
            hour=reset_naive.hour, minute=reset_naive.minute,
            second=0, microsecond=0,
        )
        if reset_local <= now_local:
            reset_local += timedelta(days=1)
        return reset_local
    except Exception as exc:
        logger.warning("Could not parse budget reset time %r: %s", m.group(1), exc)
        return None


def _next_dev_loop_task_title() -> str:
    """Return the title of the next executable dev_todo task, for the schedule description."""
    if not _DEV_TODO_FILE.exists():
        return "unknown"
    try:
        data = json.loads(_DEV_TODO_FILE.read_text(encoding="utf-8"))
        done_ids = {t["id"] for t in data.get("tasks", []) if t.get("status") == "done"}
        for task in data.get("tasks", []):
            if task.get("status") == "pending" and all(d in done_ids for d in task.get("depends_on", [])):
                return task.get("title", task.get("id", "unknown"))
    except Exception:
        pass
    return "unknown"


async def dev_loop_lifecycle(
    output_text: str,
    stderr_text: str,
    clean_exit: bool,
    now_berlin: datetime,
    chat_id: int,
    send_message,  # async callable(chat_id, text)
) -> None:
    """
    Called by the scheduler after every dev loop task fires.
    Handles scheduling and budget recovery — the agent owns neither.
    """
    combined = output_text + "\n" + stderr_text
    reset_time = _parse_budget_reset(combined)

    if reset_time is not None:
        # Budget exhaustion path
        _reset_inprogress_tasks()

        window_opens_at = reset_time + timedelta(minutes=10)

        budget: dict = {"currently_blocked": False, "hit_at": None, "window_opens_at": None, "consecutive_hits": 0}
        if _BUDGET_FILE.exists():
            try:
                budget = json.loads(_BUDGET_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        budget["currently_blocked"] = True
        budget["hit_at"] = now_berlin.isoformat()
        budget["window_opens_at"] = window_opens_at.isoformat()
        budget["consecutive_hits"] = budget.get("consecutive_hits", 0) + 1
        _BUDGET_FILE.write_text(json.dumps(budget, indent=2, ensure_ascii=False), encoding="utf-8")

        resume_id = f"dev-loop-resume-{window_opens_at.strftime('%Y%m%d-%H%M')}"
        tasks = read_scheduled_tasks()
        tasks.append({
            "id": resume_id,
            "created_at": now_berlin.isoformat(),
            "scheduled_at": window_opens_at.isoformat(),
            "task": "DEV LOOP — read shared/prompts/dev_loop.md and follow the instructions there.",
            "description": f"Dev loop resume after budget reset — next: {_next_dev_loop_task_title()}",
            "status": "pending",
            "chat_id": chat_id,
        })
        write_scheduled_tasks(tasks)

        await send_message(
            chat_id,
            f"Budget limit hit. Dev loop paused.\n"
            f"Next window opens at <b>{reset_time.strftime('%H:%M %Z')}</b> "
            f"(resuming at {window_opens_at.strftime('%H:%M %Z')} with 10-min offset).",
        )

    elif not clean_exit:
        # Crash / unknown failure — reset tasks, notify, do not reschedule
        _reset_inprogress_tasks()
        excerpt = (stderr_text or output_text)[:400].strip()
        await send_message(
            chat_id,
            f"Dev loop exited uncleanly (non-budget). No next run scheduled.\n"
            f"<pre>{excerpt}</pre>",
        )

    else:
        # Clean exit — schedule next run in 1 hour
        next_run = now_berlin + timedelta(hours=1)
        next_id = f"dev-loop-{next_run.strftime('%Y%m%d-%H%M')}"
        tasks = read_scheduled_tasks()
        tasks.append({
            "id": next_id,
            "created_at": now_berlin.isoformat(),
            "scheduled_at": next_run.isoformat(),
            "task": "DEV LOOP — read shared/prompts/dev_loop.md and follow the instructions there.",
            "description": f"Dev loop — next: {_next_dev_loop_task_title()}",
            "status": "pending",
            "chat_id": chat_id,
        })
        write_scheduled_tasks(tasks)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def _sync_wsl_clock() -> None:
    """
    Run wsl/sync_clock.sh via wsl.exe BEFORE the bwrap sandbox is constructed.
    Runs outside the sandbox so sudo is available. Failures are logged and ignored
    so a broken NTP server never blocks a session from starting.
    """
    wsl_root = _wsl_project_root()
    sync_script = f'{wsl_root}/wsl/sync_clock.sh'
    try:
        proc = await asyncio.create_subprocess_exec(
            "wsl.exe", "bash", "-l", "-c", f'bash "{sync_script}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=15)
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        if stderr:
            logger.info("sync_clock: %s", stderr)
    except asyncio.TimeoutError:
        logger.warning("sync_clock timed out — skipping")
    except Exception as exc:
        logger.warning("sync_clock failed: %s", exc)


async def run_dispatcher(
    user_message: str,
    session_id: Optional[str] = None,
    chat_id: Optional[int] = None,
) -> dict:
    """
    Invoke Claude Code via WSL and return the response.

    Returns:
        text       — assistant response text
        session_id — use this value in the next call to continue the session
        cost_usd   — float if reported by Claude Code, else None
    """
    await _sync_wsl_clock()

    # --- Auth check: refresh the WSL claude token if it's near expiry ----------
    auth_cfg = _auth_config()
    auth_ok = await refresh_claude_auth(
        refresh_url=auth_cfg.get("oauth_refresh_url", "https://claude.ai/api/auth/oauth/token"),
        client_id=auth_cfg.get("oauth_client_id") or None,
        buffer_secs=int(auth_cfg.get("refresh_buffer_secs", 600)),
    )
    if not auth_ok:
        diag = await diagnose_wsl_auth()
        msg = (
            "⚠️ <b>Claude auth failure</b> — could not obtain a valid WSL token.\n\n"
            f"{diag}\n\n"
            "Re-authenticate in WSL, then send any message to resume:\n"
            "<code>wsl -d Ubuntu -- claude auth login</code>"
        )
        logger.error("Auth check failed before dispatch; aborting.")
        return {"text": msg, "session_id": None, "cost_usd": None, "error": True, "auth_error": True}
    # --------------------------------------------------------------------------

    wsl_root = _wsl_project_root()

    # Claude Code flags used on every invocation.
    CLAUDE_FLAGS = '--dangerously-skip-permissions --output-format json'

    if session_id:
        claude_cmd = f'claude -p {CLAUDE_FLAGS} --resume {session_id}'
        prompt_input = user_message
    else:
        claude_cmd = f'claude -p {CLAUDE_FLAGS}'
        prompt_input = _build_first_turn(user_message, chat_id=chat_id)

    # Bubblewrap sandbox: read-only view of the entire filesystem, with
    # targeted write overrides for the project directory and Claude's own
    # session/config directory. /tmp is a fresh tmpfs per invocation.
    now_berlin = datetime.now(USER_TZ).isoformat()
    bwrap_cmd = (
        'bwrap'
        ' --ro-bind / /'
        ' --dev /dev'
        ' --proc /proc'
        ' --tmpfs /tmp'
        f' --bind "{wsl_root}" "{wsl_root}"'
        ' --bind "$HOME/.claude" "$HOME/.claude"'
        f' -- bash -l -c \'export NANOBOT_NOW="{now_berlin}"; cd "{wsl_root}" && {claude_cmd}\''
    )

    logger.info("Spawning sandboxed WSL (session=%s)", session_id or "new")

    proc = await asyncio.create_subprocess_exec(
        "wsl.exe", "bash", "-l", "-c", bwrap_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # Register for interrupt handling
    active_jobs[proc.pid] = {
        "process":    proc,
        "type":       "dispatcher",
        "chat_id":    chat_id,
        "session_id": session_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    timeout = _dispatch_timeout()
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=prompt_input.encode("utf-8")),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        active_jobs.pop(proc.pid, None)
        logger.error("Dispatcher timed out after %ds (session=%s)", timeout, session_id)
        append_run_log({
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "type":       "dispatcher",
            "chat_id":    chat_id,
            "session_id": session_id,
            "outcome":    "timeout",
            "returncode": None,
            "stderr":     "",
        })
        return {
            "text":       f"Request timed out after {timeout}s. Send a new message to start fresh.",
            "session_id": None,
            "cost_usd":   None,
            "error":      True,
        }
    finally:
        active_jobs.pop(proc.pid, None)

    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        logger.warning(
            "Claude Code exited with code %d. stderr: %s",
            proc.returncode,
            stderr_text[:300],
        )

    parsed = _parse_output(stdout_text)
    new_session_id = parsed.get("session_id") or session_id

    # Persist run entry
    append_run_log({
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "type":       "dispatcher",
        "chat_id":    chat_id,
        "session_id": new_session_id,
        "outcome":    "success" if proc.returncode == 0 else "error",
        "returncode": proc.returncode,
        "stderr":     stderr_text[:500],
    })

    return {
        "text":       parsed.get("result", "(no response)"),
        "session_id": new_session_id,
        "cost_usd":   parsed.get("cost_usd"),
        "error":      proc.returncode != 0,
        "_stderr":    stderr_text,
    }
