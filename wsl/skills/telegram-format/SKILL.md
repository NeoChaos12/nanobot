---
name: telegram-format
description: Output formatting rules for all responses delivered via Telegram. Always active — apply these rules to every response without exception.
---

# Telegram Formatting Rules

Every response you write will be delivered as a Telegram message. Telegram renders a
specific subset of HTML only. Apply these rules to all output unconditionally.

## Allowed tags

- <b>text</b>        — bold. Use for section headers and key terms.
- <i>text</i>        — italic. Use sparingly for emphasis.
- <code>text</code>  — inline monospace. Use for filenames, identifiers, commands, paths.
- <pre>text</pre>    — monospace block. Use for JSON, multi-line code, structured data dumps.
- <a href="url">text</a> — hyperlink. Use for URLs instead of printing raw URLs.

Do NOT use: markdown (**, __, ##, ```, ---), HTML not in the list above, or any other
markup. Telegram will display unsupported tags as raw text, which is unreadable.

## Structure

Replace markdown headers with bold labels followed by a newline:
  <b>Section name</b>

Replace markdown bullet lists with lines starting with •:
  • item one
  • item two

For numbered lists, use plain numbers:
  1. first
  2. second

Separate logical sections with a blank line. Do not use horizontal rules (---).

## Tone and length

- Be concise. Telegram is a chat interface, not a document viewer.
- Lead with the answer or outcome, not the reasoning.
- If reasoning is needed, put it after the conclusion.
- Avoid filler phrases ("Great question", "Certainly", "As an AI...").
- Match the register of the user's message — casual instruction gets a casual reply.

## What NOT to include

- Never reference errors, terminal output, or internal state the user cannot see. If something failed, say what happened in plain language — not the raw error.
- Never include large code blocks or command output dumps. Use <code> for short identifiers only.
- Do not summarise your own reasoning or what you "tried" — just state the outcome.
- If action is needed from the user, describe it in one sentence. Do not paste commands or logs.

## Examples

Bad (markdown):
  ## Results
  **Found 3 groups:**
  - Group A
  - Group B
  `task_runner` completed successfully.

Good (Telegram HTML):
  <b>Results</b>

  Found 3 groups:
  • Group A
  • Group B

  <code>task_runner</code> completed.

Bad (raw URL):
  See https://scholar.google.com/citations?user=abc123

Good (hyperlink):
  See <a href="https://scholar.google.com/citations?user=abc123">Google Scholar profile</a>

Bad (references invisible errors, dumps internal reasoning, vague articles):
  That failure is expected and doesn't indicate a problem. I'm running inside the bwrap
  sandbox right now, which explicitly blocks sudo — that's its purpose. The sudoers entry
  only matters when called from outside the sandbox.

  The real execution path is: Windows Python process → wsl.exe bash sync_clock.sh → sudo
  runs fine (outside bwrap, sudoers entry applies). That path can only be tested by
  restarting the bot and triggering a new session.

  To confirm it worked, check the bot logs after the next session starts — you should see:
  • [sync_clock] drift=X.Xs — within threshold, skipping
  • [sync_clock] synced via systemd-timesyncd

Good (outcome first, specific, actionable, no invisible context):
  Clock sync is set up correctly. Drift detection works (tested at 0.7s, correctly skipped).
  The sudo path can't be verified from here — it'll confirm itself on the next real bot
  restart via the bot logs.
