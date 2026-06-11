"""
project_registry.py -- Multi-project / multi-bot routing for the bot pool.

Maps Telegram chat IDs to project instances (state_dir, project_dir, dispatcher
prompt) via shared/config/projects.json, and resolves bot-pool tokens from
environment variables (TELEGRAM_BOT_TOKEN_DISPATCHER / _T1 / _T2).

Path layout (relative to this file's location):
  windows/src/project_registry.py  ->  parent.parent.parent = <project_root>

Both load_projects() and get_bot_tokens() re-read their source on every call
(disk / environment respectively) -- consistent with bot_config.py's _cfg().

projects.json is optional: a missing file is treated as an empty registry
rather than an error, since single-project deployments do not need a bot pool.
"""

import json
import os
from pathlib import Path
from typing import Optional

BASE = Path(__file__).parent.parent.parent
PROJECTS_CONFIG_PATH = BASE / "shared" / "config" / "projects.json"

DISPATCHER_TOKEN_ENV = "TELEGRAM_BOT_TOKEN_DISPATCHER"
POOL_TOKEN_ENVS = {
    "T1": "TELEGRAM_BOT_TOKEN_T1",
    "T2": "TELEGRAM_BOT_TOKEN_T2",
}


def load_projects() -> dict:
    """Return the project registry, re-read from disk on each call.

    Returns {"projects": {}} if projects.json does not exist.
    """
    if not PROJECTS_CONFIG_PATH.exists():
        return {"projects": {}}
    with open(PROJECTS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_project(chat_id: int) -> Optional[dict]:
    """Return the project entry for chat_id, or None if unregistered.

    The returned dict includes "project_id" alongside the registry fields
    (chat_id, state_dir, project_dir, dispatcher_prompt).
    """
    registry = load_projects()
    for project_id, entry in registry.get("projects", {}).items():
        if entry.get("chat_id") == chat_id:
            return {"project_id": project_id, **entry}
    return None


def get_bot_tokens() -> dict:
    """Return bot-pool tokens read from environment variables.

    {"dispatcher": <token or None>, "T1": <token or None>, "T2": <token or None>}

    All three are optional from this module's point of view -- callers decide
    what is required for their use case.
    """
    tokens = {"dispatcher": os.environ.get(DISPATCHER_TOKEN_ENV)}
    for name, env_var in POOL_TOKEN_ENVS.items():
        tokens[name] = os.environ.get(env_var)
    return tokens
