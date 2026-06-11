"""
state.py -- Atomic state file read/write helpers.

All writes use os.replace() (atomic on NTFS) via a .tmp intermediate.
State files live under <project_root>/state/, accessible from both Windows and WSL.

Path layout (relative to this file's location):
  windows/src/state.py  →  parent.parent.parent = <project_root>

Projects extend this module by adding domain-specific file constants and typed
accessors in a project overlay. Do not add project-specific state here.
"""

import os
import html
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

BASE       = Path(__file__).parent.parent.parent
STATE_DIR  = BASE / "state"
SHARED_DIR = BASE / "shared"

# Framework state files — present in every project using this framework.
RUN_LOG_FILE           = STATE_DIR / "run_log.json"
LESSONS_FILE           = STATE_DIR / "lessons.json"
SCHEDULED_TASKS_FILE   = STATE_DIR / "scheduled_tasks.json"
PENDING_QUESTIONS_FILE = STATE_DIR / "pending_questions.json"
CHAT_HISTORY_FILE      = STATE_DIR / "chat_history.jsonl"
CHAT_HISTORY_ARCHIVE_DIR = STATE_DIR / "chat_history_archive"
AGENTS_DIR             = STATE_DIR / "agents"

CONFIG_FILE = SHARED_DIR / "config" / "nanobot.config.json"


# ---------------------------------------------------------------------------
# Core I/O
# ---------------------------------------------------------------------------

def atomic_write(path: Path, data: Any) -> None:
    """Write JSON atomically: write to .tmp then os.replace() into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def read_json(path: Path) -> Any:
    """Read a JSON file. Returns [] if the file does not exist yet."""
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Generic typed accessors
# ---------------------------------------------------------------------------

def read_run_log() -> list[dict]:
    return read_json(RUN_LOG_FILE)

def write_run_log(data: list[dict]) -> None:
    atomic_write(RUN_LOG_FILE, data)

def read_lessons() -> list[dict]:
    return read_json(LESSONS_FILE)

def write_lessons(data: list[dict]) -> None:
    atomic_write(LESSONS_FILE, data)

def read_scheduled_tasks() -> list[dict]:
    return read_json(SCHEDULED_TASKS_FILE)

def write_scheduled_tasks(data: list[dict]) -> None:
    atomic_write(SCHEDULED_TASKS_FILE, data)

def read_pending_questions() -> list[dict]:
    return read_json(PENDING_QUESTIONS_FILE)

def write_pending_questions(data: list[dict]) -> None:
    atomic_write(PENDING_QUESTIONS_FILE, data)

def append_pending_question(question: dict) -> None:
    """Append one question to pending_questions.json."""
    questions = read_pending_questions()
    questions.append(question)
    write_pending_questions(questions)

def claim_human_input_slot(question: dict) -> bool:
    """
    Enforce the one-active-question rule.

    Checks pending_questions.json for any entry with status='pending'.
    - Found: appends question with status='buffered', returns False (caller must NOT notify).
    - Not found: appends question with status='pending', returns True (caller may notify).
    """
    questions = read_pending_questions()
    has_active = any(q.get("status") == "pending" for q in questions)
    question["status"] = "buffered" if has_active else "pending"
    questions.append(question)
    atomic_write(PENDING_QUESTIONS_FILE, questions)
    return not has_active

def resolve_pending_question(question_id: str, answer: str) -> bool:
    """Mark a pending question as answered and promote the oldest buffered question."""
    questions = read_pending_questions()
    found = False
    for q in questions:
        if q.get("id") == question_id and q.get("status") == "pending":
            q["status"] = "answered"
            q["answer"] = answer
            q["answered_at"] = datetime.now(timezone.utc).isoformat()
            found = True
            break
    if found:
        for q in questions:
            if q.get("status") == "buffered":
                q["status"] = "pending"
                logger.info("Promoted buffered question %s to pending", q.get("id"))
                break
        write_pending_questions(questions)
    return found

def ensure_agent_dir(agent_id: str) -> Path:
    """Create and return the temp directory for an agent."""
    agent_dir = AGENTS_DIR / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    return agent_dir


def append_run_log(entry: dict) -> None:
    """Append one entry to run_log.json."""
    log = read_run_log()
    log.append(entry)
    write_run_log(log)


def _chat_history_settings() -> dict:
    """
    Read chat-history rotation settings from nanobot.config.json (session block).
    These are dynamic bot parameters -- re-read on every call so /config reload
    takes effect without a restart.
    """
    defaults = {
        "idle_timeout_seconds":            1800,
        "chat_history_rotate_multiplier":  3,
        "chat_history_max_size_mb":        5,
    }
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("session", {})
        return {k: cfg.get(k, v) for k, v in defaults.items()}
    except Exception:
        return defaults


def _read_first_entry(path: Path) -> dict | None:
    """Read and parse the first JSONL line of a file. Returns None if empty/invalid."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    return None
    except OSError:
        return None
    return None


def _rotate_chat_history_if_stale() -> None:
    """
    Roll chat_history.jsonl into chat_history_archive/ if its oldest entry is
    older than (idle_timeout_seconds * chat_history_rotate_multiplier).
    Otherwise leave it in place -- new turns keep appending to the same file.
    """
    if not CHAT_HISTORY_FILE.exists() or CHAT_HISTORY_FILE.stat().st_size == 0:
        return

    settings = _chat_history_settings()
    threshold_seconds = settings["idle_timeout_seconds"] * settings["chat_history_rotate_multiplier"]

    first = _read_first_entry(CHAT_HISTORY_FILE)
    if not first or "timestamp" not in first:
        return

    try:
        first_ts = datetime.fromisoformat(first["timestamp"])
    except ValueError:
        return

    age_seconds = (datetime.now(timezone.utc) - first_ts).total_seconds()
    if age_seconds <= threshold_seconds:
        return

    CHAT_HISTORY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_name = f"chat_history_{first_ts.strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    archive_path = CHAT_HISTORY_ARCHIVE_DIR / archive_name
    if archive_path.exists():
        # Extremely unlikely (same-second rotation), but avoid clobbering.
        archive_path = CHAT_HISTORY_ARCHIVE_DIR / f"chat_history_{first_ts.strftime('%Y%m%dT%H%M%SZ')}_{os.getpid()}.jsonl"
    os.replace(CHAT_HISTORY_FILE, archive_path)


def _prune_chat_history_archive() -> None:
    """
    Enforce a total on-disk size cap (chat_history_max_size_mb) across
    chat_history.jsonl + chat_history_archive/, deleting the oldest archived
    files first. The active chat_history.jsonl is never deleted.
    """
    settings = _chat_history_settings()
    max_bytes = settings["chat_history_max_size_mb"] * 1024 * 1024

    active_size = CHAT_HISTORY_FILE.stat().st_size if CHAT_HISTORY_FILE.exists() else 0
    if not CHAT_HISTORY_ARCHIVE_DIR.exists():
        return

    archives = sorted(CHAT_HISTORY_ARCHIVE_DIR.glob("chat_history_*.jsonl"))
    total = active_size + sum(p.stat().st_size for p in archives)

    for p in archives:
        if total <= max_bytes:
            break
        size = p.stat().st_size
        try:
            p.unlink()
            total -= size
        except OSError:
            continue


def append_chat_turn(chat_id: int, session_id: str, role: str, text: str) -> None:
    """
    Append one conversation turn to chat_history.jsonl (append-only).

    Before appending, rotates chat_history.jsonl into chat_history_archive/ if
    it has grown stale (older than idle_timeout_seconds * chat_history_rotate_multiplier),
    and prunes the archive to stay under chat_history_max_size_mb total.
    """
    _rotate_chat_history_if_stale()

    entry = {
        "chat_id":    chat_id,
        "session_id": session_id,
        "role":       role,
        "text":       html.escape(text),
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }
    with open(CHAT_HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    _prune_chat_history_archive()


def _read_chat_turns(path: Path, chat_id: int) -> list[dict]:
    """Return all turns for chat_id from a single chat-history JSONL file."""
    if not path.exists():
        return []
    turns: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("chat_id") == chat_id:
                        turns.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return turns


def get_previous_session_turns(chat_id: int, n: int) -> list[dict]:
    """
    Return up to n turns from the most recent completed session for chat_id.

    Looks at chat_history.jsonl first; if it has no turns for this chat (e.g.
    rotation just happened right after the last session ended), falls back to
    the most recently archived file.
    """
    turns = _read_chat_turns(CHAT_HISTORY_FILE, chat_id)

    if not turns and CHAT_HISTORY_ARCHIVE_DIR.exists():
        archives = sorted(CHAT_HISTORY_ARCHIVE_DIR.glob("chat_history_*.jsonl"), reverse=True)
        if archives:
            turns = _read_chat_turns(archives[0], chat_id)

    if not turns:
        return []
    last_session_id = turns[-1]["session_id"]
    session_turns = [t for t in turns if t["session_id"] == last_session_id]
    return session_turns[-n:]


# ---------------------------------------------------------------------------
# Dispatcher context snapshot
# ---------------------------------------------------------------------------

def compact_snapshot(domain_fn: Callable[[], str] | None = None) -> str:
    """
    Return a short plain-text state summary injected into each new dispatcher session.
    Kept deliberately brief — a few lines, not a full dump.

    Pass an optional domain_fn() callable to prepend project-specific entity counts or
    other domain state before the framework-level sections.
    """
    log = read_run_log()

    last_run = "never"
    if log:
        last = log[-1]
        last_run = (
            f"{last.get('timestamp', '?')[:19].replace('T', ' ')} -- "
            f"{last.get('target_id', last.get('type', '?'))} "
            f"({last.get('outcome', '?')})"
        )

    lessons = [l for l in read_lessons() if not l.get("resolved", False)]
    lesson_lines = ""
    if lessons:
        lesson_lines = "\nActive lessons:\n" + "\n".join(
            f"  [{l.get('category', '?')}] {l.get('summary', '')}"
            for l in lessons[-10:]
        )

    pending_qs = [q for q in read_pending_questions() if q.get("status") == "pending"]
    pending_lines = ""
    if pending_qs:
        pending_lines = "\nPending questions awaiting your response:\n" + "\n".join(
            f"  [{q.get('id')}] {q.get('context_summary', '')} -> \"{q.get('question', '')}\""
            for q in pending_qs
        )

    scheduled = [t for t in read_scheduled_tasks() if t.get("status") == "pending"]
    scheduled_lines = ""
    if scheduled:
        scheduled_lines = "\nScheduled tasks:\n" + "\n".join(
            f"  [{t.get('id')}] at {t.get('scheduled_at', '?')[:16]} -- {t.get('description') or t.get('task', '')[:80]}"
            for t in scheduled
        )

    domain_section = (domain_fn() + "\n") if domain_fn else ""
    return (
        f"{domain_section}"
        f"Last run:     {last_run}"
        f"{lesson_lines}"
        f"{pending_lines}"
        f"{scheduled_lines}"
    )
