# nanobot — Telegram-Connected Agent Framework

A lightweight framework for running a Claude Code agent accessible via Telegram on a Windows + WSL2 machine. The bot receives messages on Telegram, routes them to a Claude Code subprocess running in WSL, and returns the response — with support for scheduled tasks, session management, and project-specific skill overlays.

Built on top of [HKUDS/nanobot](https://github.com/HKUDS/nanobot). See that repo for the full nanobot documentation.

## What this is

- **Windows bot process** (`windows/src/listener.py`): Telegram bot powered by `python-telegram-bot`. Handles incoming messages, session state, scheduled task dispatch, and auth keepalive.
- **WSL agent process**: Claude Code CLI running inside a bubblewrap sandbox. Your skills and prompts live here.
- **Project overlay**: Your domain-specific state, skills, and config sit in a separate directory — the framework provides the plumbing, you provide the prompts.

## Prerequisites

- Windows 10/11 with WSL2 (Ubuntu recommended)
- Python 3.11+ on both Windows and WSL
- [uv](https://github.com/astral-sh/uv) package manager (WSL)
- [Claude Code CLI](https://claude.ai/code) installed and authenticated in WSL
- A Telegram bot token — create one via [@BotFather](https://t.me/botfather)

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/<your-username>/nanobot.git
cd nanobot

# Copy config template and fill in your values
cp shared/config/nanobot.config.template.json shared/config/nanobot.config.json
# Edit nanobot.config.json: set TELEGRAM_BOT_TOKEN, YOUR_CHAT_ID, wsl_project_root
```

### 2. Set up WSL dependencies

Follow `wsl/SETUP.md`. The key steps:

```bash
# From inside WSL:
cd /mnt/<drive>/<path-to>/nanobot/wsl
uv sync --python 3.11 --venv ~/venvs/nanobot
~/venvs/nanobot/bin/playwright install chromium
```

### 3. Set up Windows dependencies

```powershell
# From Windows PowerShell, in the windows/ directory:
python -m venv .venv
.venv\Scripts\pip install python-telegram-bot httpx
```

### 4. Register as a startup task

Edit `tools\setup_task_scheduler.ps1` to set your installation paths, then run as Administrator:

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_task_scheduler.ps1
```

### 5. Verify

Send a message to your bot on Telegram. It should respond via the dispatcher.

## Project overlay

The framework is domain-agnostic. To build your own pipeline:

1. Create a project directory (outside this repo) containing:
   - `state/` — your domain data, `run_log.json`, `agents/`
   - `shared/prompts/dispatcher_system.md` — adapted from `shared/prompts/dispatcher_system.template.md`
   - `shared/PROTOCOL.md` — your pipeline-specific workflow
   - `wsl/skills/` — your domain-specific skill SKILL.md files
2. Set `wsl_project_root` in `nanobot.config.json` to point to this directory.
3. Claude Code runs with your project directory as its working directory.

The `dispatcher_system.template.md` contains placeholder variables (`{{USER_NAME}}`, `{{PROJECT_ROOT}}`, etc.) to fill in for your use case.

See `examples/research-discovery/` for a complete worked example: a pipeline that discovers research groups at German universities working on user-specified topics. It shows how to extend `state.py` with domain entities, fill in `PROTOCOL.md`, and adapt the dispatcher prompt.

## Included skills

Generic skills in `wsl/skills/`:

| Skill | Role |
|---|---|
| `telegram-format` | HTML formatting rules for all Telegram responses |
| `research-planning` | Refine a vague goal into a structured research outline |
| `task-planning` | Break an outline into concrete executor tasks |
| `task-executor` | Dispatch subagents, manage the review loop |
| `web-researcher` | Search web, OpenAlex, Semantic Scholar, GitHub |
| `quality-reviewer` | Two-stage review gate for executor outputs |

## Bot commands

| Command | Description |
|---|---|
| `/help` | List all commands |
| `/end` | Close the current session |
| `/tasks` | List queued and failed tasks |
| `/tasks cancel T1` | Cancel a queued task |
| `/tasks retry F1` | Requeue a failed task |
| `/schedule 2h prompt` | Queue a task to run in 2 hours |
| `/keepalive` | Show OAuth keepalive status |
| `/authstatus` | Show WSL Claude token health |
| `/reauth` | Attempt token refresh |
| `/config` | Show hot-reloadable config values |
| `/restart` | Restart the bot process |

## Windows service (alternative to Task Scheduler)

`tools/winsw.xml` provides a [WinSW](https://github.com/winsw/winsw/releases) service config. Download `WinSW.exe` into `tools/`, edit the XML paths, then:

```cmd
tools\WinSW.exe install
tools\WinSW.exe start
```

## License

MIT — see [LICENSE](LICENSE).
