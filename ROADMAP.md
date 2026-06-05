# ROADMAP — Bot Framework & Research Pipeline

Canonical backlog. Supersedes: `planned-work.md`, `consolidated-backlog.md`.
Sources: `state/issues.json`, `state/lessons.json`, `docs/planned-work.md`, `docs/consolidated-backlog.md`, `docs/agent-notes.md`.
*Last updated: 2026-06-04*

---

## Bugs (P1/P2)

### BUG-001 — /tasks cancel ignores failed tasks
**Component:** `windows/src/commands/cmd_tasks.py`
**Priority:** P2
**Detail:** When a task has `status=failed`, `/tasks cancel Fn` responds "is already failed" and does nothing. Correct behaviour: cancelling a failed task should delete it from `scheduled_tasks.json` entirely. No archival needed. Also fix the error message to hint at options: "failed tasks can be retried (`/tasks retry Fn`) or deleted (`/tasks cancel Fn`)".
**Source:** `state/issues.json` issue-001, `docs/planned-work.md` BUG-001

### BUG-002 — Concurrent dispatcher invocations not serialised (partial fix)
**Component:** `windows/src/dispatcher.py`, `windows/src/bot_state.py`
**Priority:** P2
**Detail:** `on_message` had no lock — if a second user message arrived while a dispatch was in-flight, a second Claude subprocess spawned concurrently, causing interleaved state writes, `session_id` confusion, and doubled token spend. Immediate patch applied 2026-06-03: `dispatch_lock` in `bot_state` serialises all dispatcher calls; bot replies with a "busy" notice and drops the overlapping message. Queuing (rather than dropping) is deferred to a long-term session-management refactor.
**Source:** `state/issues.json` issue-002

---

## Pipeline Fixes

### FIX-001 — dev_loop: emphatic terminal state ✓ RESOLVED
**Component:** `shared/prompts/dev_loop.md`
**Priority:** P1
**Detail:** After all tasks completed, the dev_loop agent invented new phases and simulated user approval. Fixed 2026-06-04: added explicit "DO NOT invent new tasks, phases, or work. DO NOT simulate Archit approving anything." to the terminal-state section (Step 2).
**Source:** `state/lessons.json` dev-loop-hallucinated-tasks-2026-06-03

### FIX-002 — dev_loop: needs_review routing UX mismatch
**Component:** Architectural / UX
**Priority:** P2
**Detail:** When dev_loop hits a `needs_review` gate, it writes a pending_question and stops. Unblocking requires Archit to message the bot, which reaches the dispatcher (not the paused agent — that is gone). The UX implies "reply to resume" but there is no prompt telling Archit to send a message. Options: (a) remove needs_review gates for routine phases, keeping only actual decision points; (b) make the dispatcher state summary show pending dev-loop questions as clearly resumable yes/no prompts.
**Source:** `state/lessons.json` dev-loop-needs-review-routing-2026-06-02

### FIX-003 — Stale dev-loop scheduled task cleanup ✓ RESOLVED
**Component:** `state/scheduled_tasks.json`
**Priority:** P3
**Detail:** Task `dev-loop-20260602-0118` had `status=pending` with `fired_at` already set. Resolved 2026-06-04: entry marked `cancelled` with explanatory note in `scheduled_tasks.json`.
**Source:** `state/lessons.json` dev-loop-scheduled-task-sync-2026-06-02

---

## Framework Improvements

### BACK-001 — Sub-agent lesson writing
**Component:** `wsl/skills/task-executor/SKILL.md`
**Priority:** P3
**Detail:** Executor subagents (web-researcher, quality-reviewer) run as isolated `claude -p` processes and cannot write to `lessons.json`. Learnings are only captured if task-executor explicitly surfaces them. Options: (a) add a lesson-writing step to task-executor at plan completion; (b) define a structured `lesson_hints` field in executor status reports that task-executor pipes through to the dispatcher.
**Source:** `docs/planned-work.md` BACK-001, `docs/consolidated-backlog.md` BACK-001

### BACK-002 — Lesson-writing trigger rules
**Component:** dispatcher / task-executor
**Priority:** P3
**Detail:** Lesson extraction should fire on specific events rather than always or never. Proposed triggers: BLOCKED escalation from task-executor; quality review failure + retry; ≥5 user messages in a session; pending_question answered during a session.
**Source:** `docs/planned-work.md` BACK-002, `docs/consolidated-backlog.md` BACK-002

### BACK-003 — OAuth refresh endpoint hardening
**Component:** `windows/src/wsl_auth.py`
**Priority:** P3
**Detail:** `wsl_auth.py` POSTs to `https://claude.ai/api/auth/oauth/token` — inferred, not confirmed. If the endpoint changes, bot auth silently breaks. Action: document that `auth.oauth_refresh_url` in `nanobot.config.json` must be audited whenever the Claude CLI auth flow changes. The refresh token also has a finite lifetime (weeks–months); plan for periodic manual re-auth.
**Source:** `docs/planned-work.md` BACK-003, `docs/consolidated-backlog.md` BACK-003

### BACK-004 — Install `gh` CLI in WSL
**Component:** environment
**Priority:** P3
**Detail:** `gh` is not available in WSL and is needed for GitHub operations (e.g. setting default branch). Install:
```
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | \
  sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && \
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | \
  sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
  sudo apt update && sudo apt install gh
```
**Source:** `docs/planned-work.md` BACK-004, `docs/consolidated-backlog.md` BACK-004

### BACK-005 — Verify wsl_auth.py first-run OAuth refresh
**Component:** `windows/src/wsl_auth.py`
**Priority:** P3
**Detail:** After bot restart: send `/authstatus` and confirm credentials file is read correctly. Watch logs for `wsl_auth: token refreshed` on the next near-expiry cycle. This step was listed as pending in the 2026-05-29 high-priority fixes plan and has not been explicitly verified.
**Source:** `docs/consolidated-backlog.md` BACK-005

### BACK-006 — Rolling logs for agent tool calls, script execution, and chat history
**Component:** `windows/src/`, `wsl/`
**Priority:** P3
**Detail:** Implement a rolling log system covering three areas: (1) agent tool calls (which tools were invoked, by which agent, with what args/result summary); (2) script execution (subprocess calls, exit codes, stdout/stderr snippets); (3) chat history (per-session log rotation so `chat_history.jsonl` does not grow unbounded). Rolling policy: cap by size (e.g. 5 MB) or by age (e.g. 30 days), keeping N most recent segments. Enables post-hoc debugging without disk bloat.
**Source:** Archit, 2026-06-05

---

## Deferred

### DEF-001 — Concurrent message queuing (full implementation)
**Component:** `windows/src/listener.py`, `windows/src/bot_state.py`
**Detail:** The current dispatch_lock drops overlapping messages with a "busy" notice. A proper message queue would hold them and process in order, enabling uninterrupted multi-turn flows. Deferred to a session-management refactor.
**Source:** `state/issues.json` issue-002 (partial fix notes)

### DEF-002 — dev_loop pending_question UX redesign
**Component:** dispatcher, dev_loop
**Detail:** The needs_review gate could be redesigned to be fully dispatcher-native rather than relying on the subagent's pending_question mechanism. This would eliminate the routing mismatch (FIX-002) entirely but requires non-trivial architectural change.
**Source:** `state/lessons.json` dev-loop-needs-review-routing-2026-06-02

---

## One-time manual actions

| # | Action | Status |
|---|--------|--------|
| 1 | Set `clean-main` as default branch on GitHub (Settings → Branches) | Pending |
| 2 | Verify `/authstatus` after next bot restart (BACK-005) | Pending |
