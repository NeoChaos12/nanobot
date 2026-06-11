This file provides guidance to AI coding agents working with this repository.

## Project Overview

This is a lightweight framework for running a Claude Code agent accessible via Telegram on a Windows + WSL2 machine. The bot receives messages on Telegram, routes them to a Claude Code subprocess running in WSL, and returns the response — with support for scheduled tasks, session management, and project-specific skill overlays.

Built on top of [HKUDS/nanobot](https://github.com/HKUDS/nanobot).

## Architecture

### Core components

- **Windows bot process** (`windows/src/listener.py`): Telegram bot powered by `python-telegram-bot`. Receives messages, manages sessions, dispatches scheduled tasks, forwards messages to WSL via subprocess.
- **WSL agent process**: Claude Code CLI invoked by the Windows listener. Runs inside the project directory with skills and prompts loaded from context.
- **Shared config** (`shared/config/nanobot.config.json`): Single JSON config consumed by both Windows and WSL sides. Contains bot token, chat ID, WSL paths, and runtime parameters.
- **Project overlay**: Domain-specific state, skills, and config live in a separate directory (`wsl_project_root` in config). The framework provides plumbing; user provides prompts.

### File layout

```
windows/src/listener.py     — Telegram bot, session loop, scheduled task runner
windows/src/bot_config.py   — Config loader (reads shared/config/nanobot.config.json)
windows/src/commands.py     — /help, /tasks, /schedule, /config, /restart handlers
windows/src/auth.py         — WSL Claude token health checks and keepalive
shared/config/              — nanobot.config.json (gitignored), .template.json
shared/prompts/             — dispatcher_system.template.md and other prompt templates
shared/schemas/             — JSON schemas for state files (gitignored)
wsl/skills/                 — Generic reusable skill SKILL.md files
wsl/pyproject.toml          — WSL Python dependencies (playwright, httpx, etc.)
wsl/SETUP.md                — WSL setup instructions
tools/                      — Windows Task Scheduler / WinSW service setup scripts
tests/                      — pytest suite for the Windows bot components
nanobot/                    — Minimal Python package stub (version metadata only)
```

## Development Commands

```bash
# Run tests (WSL)
cd /path/to/framework-repo
pytest tests/ -v

# Lint (WSL)
ruff check windows/src/ wsl/

# Start the bot (Windows PowerShell, from windows/)
.venv\Scripts\python src\listener.py
```

## Key Conventions

- All Telegram responses use HTML parse mode. Formatting rules live in `wsl/skills/telegram-format/SKILL.md`.
- Scheduled tasks are written to `state/scheduled_tasks.json` by the dispatcher and polled by the Windows listener.
- Skills are Markdown files (`SKILL.md`) that Claude Code loads into context. They are not Python modules.
- The `shared/config/nanobot.config.json` is gitignored; users copy from the `.template.json`.
- New non-one-off scripts/services must log at key workflow checkpoints (DEBUG level,
  silenceable via `NANOBOT_LOG_LEVEL=INFO`) into a rolling log. Default retention is
  30 days, rotating one day's file at a time (so each rotation only drops ~1 day of
  history, not the whole log), with a 5MB-per-day safety cap; both are configurable.
  PowerShell scripts in `tools/` dot-source `tools/lib/logging.ps1` and call
  `Write-Log -Path ".../name.log"` (writes to `name-yyyy-MM-dd.log`); Python code
  uses `logging` with a `TimedRotatingFileHandler` (daily, 30 backups) on the same
  defaults.

## Project-Specific Overlay

To build a domain-specific pipeline on top of this framework, create a project directory (outside this repo) containing:

1. `shared/prompts/dispatcher_system.md` — adapted from `shared/prompts/dispatcher_system.template.md`
2. `shared/PROTOCOL.md` — pipeline-specific workflow
3. `wsl/skills/` — domain-specific skill SKILL.md files
4. `state/` — domain data and run logs

Set `wsl_project_root` in `nanobot.config.json` to point to this directory.

See `examples/german-university-discovery/` for a worked example.
