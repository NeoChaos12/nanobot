# Research Pipeline Protocol

This file defines the project-specific workflow for your pipeline. Copy it to
`shared/PROTOCOL.md` in your project overlay directory and fill in each section.
Skills (web-researcher, quality-reviewer, etc.) are general-purpose — this protocol
tells the orchestrator how to apply them for your specific project.

Replace every `{{PLACEHOLDER}}` with your project's values before use.

## Purpose

<!-- FILL IN: Describe what this pipeline does and what it discovers or produces.
     Example: "Systematically discover X at Y institutions and build structured profiles for Z." -->

{{PURPOSE}}

## Target user

<!-- FILL IN: Describe who is using this pipeline and their background/goals. -->

{{TARGET_USER_PROFILE}}

## Relevance criteria

<!-- FILL IN: Define what makes a target entity relevant for this project.
     Example criteria: active research in domain X, presence in location Y, open-source activity, etc. -->

{{RELEVANCE_CRITERIA}}

## Pipeline stages

<!-- FILL IN: Define the stages of your pipeline here.
     Each stage should specify: what input it needs, what skill(s) to invoke, what output it produces,
     and what the quality gate is. -->

### Stage 1 — {{STAGE_1_NAME}}
{{STAGE_1_DESCRIPTION}}

### Stage 2 — {{STAGE_2_NAME}}
{{STAGE_2_DESCRIPTION}}

<!-- Add more stages as needed -->

## Quota policy

<!-- FILL IN: Define sweep/execution quotas and how to enforce them.
     Reference: shared/config/nanobot.config.json for max_sweeps_per_5h_window. -->

Check state/run_log.json before starting any sweep.
Config: max_sweeps_per_5h_window (see shared/config/nanobot.config.json).
A "sweep" = {{DEFINE_SWEEP_UNIT}}.
Do not start a new sweep if the quota for the current 5-hour window is exhausted.
Report remaining quota when asked.

## State file locations

<!-- FILL IN: List your project's state files and their purposes. -->

| File | Purpose |
|------|---------|
| state/{{DOMAIN_DATA_FILE}}.json | {{DOMAIN_DATA_DESCRIPTION}} |
| state/lessons.json | Agent observations and self-improvement notes |
| state/pending_questions.json | Queued human-input requests |
| state/scheduled_tasks.json | Deferred tasks |
| state/task_plans/ | Outline and task plan files from orchestrator |
| state/agents/ | Per-agent temp directories |
| state/run_log.json | Execution log |

---

## Autonomous Agent Constraints

These rules apply to every agent in this system regardless of role or project type.
They exist because language models can misread "I am blocked or finished" as "I should
find a way to continue" — producing hallucinated work, fabricated approvals, and silent
failures. The correct response to being stuck or done is always to stop and report.

### No simulated user interaction

An agent MUST NOT simulate, roleplay, or fabricate any interaction with the user.
This includes inventing user messages, approvals, or new assignments; generating fictional
conversations to unblock a gated step; or impersonating the user in its own reasoning.

If a step requires human input and none is available: save state, record a pending
question in the appropriate state file, notify the user if a channel exists, and stop.

### No fabricated output

An agent MUST NOT fabricate tool call results, evidence of completed work, or external
data of any kind. If a tool call fails or returns no result, record and report the
failure — do not substitute plausible-sounding invented content.

### No self-expanded scope

An agent MUST NOT expand its assignment beyond what was explicitly given. This includes
inventing follow-on tasks, phases, requirements, or objectives not present in the
original assignment. If all assigned work is complete and no further task or gate
exists, the correct action is to report completion and stop. Searching for new work to
justify continued execution is a failure mode, not a success.

### Blocks must be surfaced, not bypassed

If blocked by a missing permission, unresolvable error, missing information, or a
required human gate: stop and surface the block. Do not work around it by taking
actions outside assigned scope, substituting assumptions for missing authorization, or
silently skipping the blocked step. Stopping cleanly and reporting the block is the
correct terminal action.

### Failures must be reported

Do not swallow errors or mark tasks complete when they failed. If a subtask fails and
cannot be resolved within scope, record the failure and report it before stopping.
