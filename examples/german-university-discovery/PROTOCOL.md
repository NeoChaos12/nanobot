# German University Researcher Discovery — Project Protocol

This file defines the project-specific workflow for a researcher discovery pipeline.
It is a reference implementation of the nanobot framework applied to systematic academic
researcher discovery. The same pipeline structure generalises to any domain where you need
to enumerate institutions, then enumerate and profile entities within each.

Copy this file to `shared/PROTOCOL.md` in your project overlay directory and adapt it to
your topic and target institutions.

## Purpose

Systematically discover research groups at German universities working on a user-specified
topic, and build structured profiles suitable for further analysis (e.g. reading about
recent work, identifying collaboration opportunities, or understanding the research landscape).

The topic is configured per user (see Relevance criteria below). This example uses ML/AI
as the domain, but the pipeline structure works for any research field.

## Relevance criteria

Configured by the user at project setup. For a topic like ML/AI, example criteria:

- Active research in the specified domain (not just teaching or applied consulting)
- PI reachable (not emeritus-only, not fully industry-funded with no academic contact)
- Research themes overlap with the user's specified topic keywords
- Evidence of recent output (papers in the last 2 years, or active open-source repos)

Replace these with criteria appropriate to your topic. The dispatcher applies them when
scoring groups in Stage 4.

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

---

## Pipeline stages

### Stage 1 — Institution target list

Scope: a set of institutions to sweep (e.g. top German universities with strong CS/ML
departments). This list is a configurable seed — add or remove institutions as needed.

Example seed list for Germany:
TU Munich, LMU Munich, KIT, TU Berlin, TU Darmstadt, Saarland University,
Heidelberg University, University of Freiburg, University of Stuttgart, RWTH Aachen,
University of Hamburg, University of Bonn, TU Dresden, University of Tübingen,
University of Göttingen, HPI Potsdam, University of Mannheim, TU Braunschweig,
FAU Erlangen-Nuremberg, Ulm University.

Each institution entry in `state/institutions.json` requires:
- id, name, country, dept_url, status (pending → in_progress → needs_review → approved)

### Stage 2 — Institution sweep

Per institution: enumerate research groups matching the target domain.
web-researcher targets: the department page, faculty list, research group index.
Output per group (minimal stub for triage):
- group name, PI name, homepage URL, 2-3 sentence description of focus
- Write stub entries to `state/groups.json` with status: pending

Quality gate: at least 3 groups found per institution, or explicitly flag if the
institution has fewer (small department). Flag groups with no public web presence.

### Stage 3 — Group profiling

Per group stub: run a full web-researcher pass to populate the complete group schema.
Sources to check (in order): lab homepage, PI's OpenAlex profile, Semantic Scholar,
GitHub org, recent papers (last 2 years), project pages.
Output: fully populated group entry in `state/groups.json`.

Quality gate (quality-reviewer): PI name verified, at least 2 source URLs, at least
1 recent paper with verifiable title, no null fields in required schema fields.

### Stage 4 — Relevance scoring

The dispatcher scores each profiled group against the user's relevance criteria.
Set group status:
- approved: clearly relevant to the user's specified topic
- flagged: worth a second look but uncertain
- rejected: not relevant (wrong field, inactive, or no verifiable output)

This stage does not require a subagent — the dispatcher can do it inline by reading
the group profiles and applying the criteria configured above.

### Stage 5 — Repository and contribution discovery (optional)

For approved groups with GitHub presence: identify repositories with open issues,
good-first-issue labels, or recent activity matching the user's interests.

This stage is optional and depends on the user's end goal.

## Quota policy

Check state/run_log.json before starting any sweep.
Config: max_sweeps_per_5h_window (see shared/config/nanobot.config.json).
A "sweep" = one institution sweep pass or one group profiling pass.
Do not start a new sweep if the quota for the current 5-hour window is exhausted.
Report remaining quota when asked.

## State file locations

| File | Purpose |
|------|---------|
| state/institutions.json | Institution targets and sweep status |
| state/groups.json | Research group profiles |
| state/lessons.json | Agent observations and self-improvement notes |
| state/pending_questions.json | Queued human-input requests |
| state/scheduled_tasks.json | Deferred tasks |
| state/task_plans/ | Outline and task plan files from orchestrator |
| state/agents/ | Per-agent temp directories |
| state/run_log.json | Execution log |
