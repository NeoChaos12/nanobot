# Skills Index

This file lists all available skills, their layer, and when to invoke them.
Read the corresponding SKILL.md before invoking any skill.

Skills marked **always-active** apply without being explicitly invoked.
Skills marked **deprecated** should not be used.

Copy this file to `wsl/skills/SKILLS_INDEX.md` in your project and adapt the rows below.

---

## Always-active

| Skill | File | When it applies |
|---|---|---|
| telegram-format | telegram-format/SKILL.md | Every response — apply formatting rules unconditionally |

---

## Dispatcher layer (you invoke these)

| Skill | File | When to invoke |
|---|---|---|
| research-planning | research-planning/SKILL.md | User gives a high-level goal that needs scoping and outline approval |
| task-planning | task-planning/SKILL.md | An approved outline needs to be broken into concrete executor tasks |
| task-executor | task-executor/SKILL.md | A task plan exists and is ready to execute |

---

## Executor layer (invoked by task-executor, not directly by dispatcher)

| Skill | File | What it does |
|---|---|---|
| web-researcher | web-researcher/SKILL.md | Searches web and other sources for a target entity |
| quality-reviewer | quality-reviewer/SKILL.md | Reviews executor output against spec; returns PASS or FAIL |

---

## Domain-specific executor skills

| Skill | File | What it does |
|---|---|---|
| {{DOMAIN_SKILL}} | {{DOMAIN_SKILL}}/SKILL.md | {{DESCRIPTION}} |

---

## Utility / methodology

| Skill | File | When to invoke |
|---|---|---|
| debug-diagnosis | debug-diagnosis/SKILL.md | Any time a bug or unexpected system behaviour needs diagnosing — enforces evidence-first, fix-last discipline |

---

## Deprecated (do not use)

| Skill | Replaced by |
|---|---|
| {{OLD_SKILL}} | {{NEW_SKILL_OR_DESCRIPTION}} |

---

## Adding a new skill

1. Create `wsl/skills/<name>/SKILL.md` with frontmatter (`name`, `description`) and full instructions.
2. Add a row to the appropriate section above.
3. If the skill should be invoked autonomously by the dispatcher, add a trigger condition to the dispatcher system prompt.
