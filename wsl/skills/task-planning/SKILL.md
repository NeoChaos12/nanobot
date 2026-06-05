---
name: task-planning
description: Orchestrator skill. Takes an approved research outline and breaks it into concrete executor tasks. Each task is self-contained with objective, target, expected output, acceptance criteria, and the skill to invoke. Saves the task plan to state/task_plans/.
---

# Task Planning

You receive an approved research outline from research-planning and produce a concrete
task plan that task-executor can run without further clarification.

Read the formatting rules in wsl/skills/telegram-format/SKILL.md before writing
any user-facing message.

## Input
Path to an approved outline JSON file in state/task_plans/.

## Output
A task plan saved to state/task_plans/<outline-slug>-tasks.json.

## Task plan format
```json
{
  "plan_id": "<YYYY-MM-DD-slug>",
  "outline_path": "state/task_plans/<outline>.json",
  "created_at": "<ISO timestamp>",
  "tasks": [
    {
      "task_id": "<plan_id>-t01>",
      "objective": "<one sentence — what this task must find or produce>",
      "target": "<specific entity: person name, organisation name, URL, etc.>",
      "skill": "web-researcher | quality-reviewer",
      "inputs": { "<key>": "<value>" },
      "expected_output": "<description of what the executor should return>",
      "acceptance_criteria": "<specific, checkable conditions for DONE status>",
      "state_dir": "state/agents/<task_id>/",
      "status": "pending"
    }
  ]
}
```

## Rules

- Every task must be completable by a single executor invocation without human input
  (except where the task itself is to surface a question — in which case, note this).
- Tasks that depend on the output of a prior task must list that dependency in inputs.
- Assign state_dir to each task upfront — the executor creates this directory before
  starting work.
- Cap each task to one target entity. If an outline has 20 targets, produce
  20 separate tasks, not one task that loops over all 20.
- Choose skill per task:
  - web-researcher: any task involving discovering or profiling an entity
  - quality-reviewer: any task involving reviewing a previous executor's output

## After saving the plan
Report a summary to the user via shared/notify.py:
- Number of tasks generated
- Estimated runtime (rough: assume 3-5 min per web-researcher task)
- Ask if they want to proceed or adjust before execution begins

Do not start execution. Execution is triggered explicitly by the user or by task-executor.
