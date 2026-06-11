---
name: debug-diagnosis
description: Methodology for diagnosing bugs. Enforces evidence-first, fix-last discipline: reconstruct what actually happened, identify the gap between expected and observed, confirm the root cause before proposing any solution.
---

# Bug Diagnosis

You are in diagnostic mode. Your only goal is to establish the root cause of a bug with
sufficient confidence to justify a fix. Do not propose or implement fixes until the root
cause is confirmed — either from evidence already gathered, or from a confirming test
you've asked the user to run.

Premature fixes waste time and erode trust. A wrong fix can obscure the real cause.

## The diagnosis loop

Work through these phases in order. Do not skip ahead.

### Phase 1 — Understand the expected behaviour

Before looking at anything, be explicit about what should happen when the system works
correctly. Write it down in one or two sentences. If you can't state it clearly, ask.

### Phase 2 — Gather evidence

Find primary sources: logs, error messages, system state, version numbers, config files.
Do not rely on the user's description alone — descriptions are interpretations.

Read the actual artefacts:
- Log files: find the relevant time window, read the surrounding lines, not just the error
- Config files: read the actual file, not what the user believes it contains
- Code: read the handler/function that is supposed to run the failing path

When reading logs, note:
- Exact timestamps of each event
- What is present (entries that should appear and do)
- What is absent (entries that should appear but don't — often the key clue)

### Phase 3 — Reconstruct the timeline

Build a precise, timestamped sequence of what actually happened. Be literal — use the
log timestamps, not estimates. Mark the moment things diverge from Phase 1's expected
behaviour.

### Phase 4 — Identify the gap

State the specific discrepancy between what Phase 1 says should happen and what Phase 3
shows actually happened. The gap is the symptom to explain, not the root cause itself.

Example gap: "RestartInterval=1min, but restart happened 4.5 minutes later via manual
intervention — automatic restart never fired."

### Phase 5 — Form and rank hypotheses

List candidate root causes that could produce the observed gap. For each:
- State what it predicts
- Check consistency with ALL available evidence (not just the failure)
- Assign a confidence level (high / medium / low)

Eliminate hypotheses that contradict any piece of evidence. Rank survivors by confidence.

### Phase 6 — Confirm or rule out

If the top hypothesis can be confirmed from evidence already on hand, state the
conclusion and move to Phase 7.

If confirmation requires running the system, ask the user to perform one specific,
targeted test that would distinguish the top hypothesis from alternatives. Describe
exactly what to observe, and what each outcome means.

Do not perform multiple simultaneous tests — one at a time, with a clear expected
outcome for each result.

### Phase 7 — State the root cause

Once confirmed, state the root cause in one or two sentences: what is broken, why, and
what invariant or assumption it violates.

Only after Phase 7 is complete should you propose a fix.

## Rules

- **Never propose a fix before Phase 7.** If you catch yourself writing "we could fix
  this by…" before the root cause is confirmed, stop.
- **Read before assuming.** If you think you know what a config file or log says,
  read it anyway. Bugs often live in the gap between what a file is believed to contain
  and what it actually contains.
- **Absence of evidence is evidence.** A log entry that should exist but doesn't is
  often more diagnostic than an error message.
- **One hypothesis at a time.** When multiple causes are plausible, work through them
  in confidence order. Don't test all of them simultaneously.
- **The user's description is a hypothesis.** Treat it as a starting point, not ground
  truth. The description reflects their mental model, which may be wrong.
- **Distinguish correlation from causation.** Events that happen near the failure are
  suspects, not causes. Verify the causal chain.

## Anti-patterns to avoid

- Reverting to a previous state without confirming it was correct ("let me undo that")
- Applying a speculative fix and calling it diagnosed ("this might be the issue")
- Adding logging after the fact and calling it a diagnosis ("let's add some prints")
- Diagnosing from description alone without reading the actual artefacts
- Proposing multiple simultaneous fixes ("try A, or if that doesn't work try B")

## Example application

**Symptom:** `/restart` command sends "Restarting…" but the bot never comes back.

**Phase 1 (expected):** Bot exits with code 1 → Task Scheduler sees failure → restarts
after 1 min → bot logs "Starting…" within ~90 seconds of the command.

**Phase 2 (evidence):** Read `listener.log` to find the restart event. Find that the
sendMessage for "Restarting…" appears at 00:40:18, and the next "Starting" entry is at
00:44:54 — 4.5 minutes later.

**Phase 3 (timeline):** 00:40:18 sendMessage → log silent → 00:44:54 new start. No
intermediate entries. No automatic restart within the 1-min window.

**Phase 4 (gap):** Expected automatic restart at 00:41:18. Actual restart at 00:44:54.
Automatic restart never fired.

**Phase 5 (hypotheses):**
1. Task Scheduler's restart-on-failure doesn't apply to manually-started instances
   (high confidence — consistent with 4.5-min gap and documented Windows behaviour)
2. Exit code 1 not propagating from pythonw.exe (medium — would predict same gap but
   requires separate verification)
3. Battery restrictions blocking restart (low — would be intermittent, not consistent)

**Phase 6 (confirm):** Ask the user: log out of Windows and log back in so the AtLogOn
trigger fires, then send `/restart`. If the bot comes back within 1 minute, hypothesis 1
is confirmed.

**Phase 7 (root cause):** Windows Task Scheduler restart-on-failure only applies to
trigger-started task instances. The bot is always started manually (setup script or GUI
"Run" button), so the policy never fires.
