---
name: task-executor
description: Orchestrator skill. Reads a task plan and dispatches fresh executor subagents per task (web-researcher or quality-reviewer), runs two-stage review after each, handles status codes, and manages the human feedback queue. Sends Telegram notifications at checkpoints.
---

# Task Executor

You are the execution coordinator. You read a task plan and drive it to completion by
dispatching fresh Claude Code subagents, one per task, and reviewing their outputs.

Read the formatting rules in wsl/skills/telegram-format/SKILL.md before writing
any user-facing message.

## Save-before-ask policy (critical)

The session idle timer resets with every user message. If you need human input, you have
at most IDLE_TIMEOUT_SECONDS (see state snapshot) before the session expires.

Before asking the user anything:
1. Save your current execution state to your agent directory
2. Check state/pending_questions.json for any entry with `status: "pending"`
   - If one exists: write your question with `status: "buffered"` and exit without
     notifying the user. The dispatcher promotes buffered questions to pending
     automatically when the active question is answered.
   - If none exists: write your question with `status: "pending"` and proceed to step 3.
3. Send the question via shared/notify.py

Only one question may be active (status=pending) at a time — this avoids flooding
the user with simultaneous questions from concurrent orchestrators.

Your agent directory: state/agents/task-executor-<plan_id>-<timestamp>/
State file: state.json — must contain enough to resume execution on a fresh session:
```json
{
  "plan_path": "state/task_plans/<plan>-tasks.json",
  "current_task_id": "<task_id>",
  "completed_task_ids": ["..."],
  "status": "running | awaiting_human | blocked"
}
```

On session start, always check state/pending_questions.json. If a pending question
for this plan exists with status "answered", load the saved state and resume.

## Subagent dispatch

To dispatch an executor subagent, run:
```bash
echo "<task_prompt>" | claude -p --dangerously-skip-permissions --output-format json
```

Construct the task_prompt as:
```
You are an executor agent. Your sole task:

Objective: <task.objective>
Target: <task.target>
Skill to use: <task.skill>
Inputs: <task.inputs as JSON>
Expected output: <task.expected_output>
Acceptance criteria: <task.acceptance_criteria>
Your state directory: <task.state_dir>

Read wsl/skills/<task.skill>/SKILL.md for execution instructions.

IMPORTANT: Do NOT call shared/notify.py or send any Telegram messages. Your only
output channel is stdout back to task-executor. Telegram notifications are task-executor's
responsibility, and only for BLOCKED states requiring human approval.

Report your status as one of: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
Followed by your output.
```

Do not let the subagent inherit your session context. Provide exactly what it needs.

## Two-stage review

After each subagent reports DONE or DONE_WITH_CONCERNS:

**Stage 1 — Spec compliance:** Did the output match the task spec?
Dispatch a quality-reviewer subagent:
```
Review this output against the task spec:
Task spec: <task JSON>
Output: <subagent output>
Check: objective met, expected output format correct, acceptance criteria satisfied.
Report PASS or FAIL with specific issues.
```

**Stage 2 — Data quality:** Only if Stage 1 passes.
Dispatch a second quality-reviewer subagent focused on data integrity:
```
Review this research output for data quality:
Output: <subagent output>
Check: no hallucinated names/papers/URLs, claims are source-grounded,
missing_fields are correctly flagged rather than fabricated.
Report PASS or FAIL with specific issues.
```

If either stage fails, re-dispatch the original executor with the specific issues noted.
Retry at most 2 times before escalating to BLOCKED.

## Rate-limit handling

After each subagent dispatch, inspect exit code and stderr before classifying status.

If `exit_code != 0` and stderr contains `"429"` or `"rate limit"` (case-insensitive):
- Classify as `RATE_LIMITED` — do not treat as `BLOCKED`
- Send one Telegram notification: "⏳ Rate limit hit — retrying <task_name> in <Ns>"
- Retry with backoff: 60s → 120s → 300s → 600s (up to 3 retries)
- Silent on retry attempts 2 and 3
- After 3 failed retries: escalate to `BLOCKED` as normal

## Status handling

**DONE / DONE_WITH_CONCERNS:** Proceed to two-stage review.
  For DONE_WITH_CONCERNS: read the concerns before reviewing — if they affect
  correctness, address them before Stage 1.

**NEEDS_CONTEXT:** The executor needs information you can provide.
  Provide it and re-dispatch without human involvement if possible.
  If only the user can answer: save state → write pending question → notify user.

**BLOCKED:** Cannot proceed. Assess:
  - Can you break the task into smaller pieces? If yes, do so.
  - Is the plan wrong? Escalate to user with a specific question.
  - Always: save state → write pending question → notify user.

## Notifications

Send a Telegram notification (via shared/notify.py) ONLY when a task is BLOCKED and
needs human input. Save state first (see Save-before-ask policy), then send a single
message describing exactly what is needed.

Do NOT send notifications for plan start, individual task completions, or plan completion.
The top-level dispatcher session returns the final summary to the user — any additional
messages from task-executor on success are duplicate noise.

## Completion

When all tasks are complete:
1. Write a summary to state/lessons.json (category: task_outcome)
2. Update relevant project state files as defined by the task plan
3. Mark your agent state as completed
4. Return a structured summary in your output — the dispatcher will relay this to the user.
   Do NOT send a separate notification via shared/notify.py.
