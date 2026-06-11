"""
TDD tests for windows/src/project_registry.py.

These tests define the required behaviour of the bot-pool project registry:
  (a) loading shared/config/projects.json -- a mapping of project_id ->
      {chat_id, state_dir, project_dir, dispatcher_prompt}
  (b) resolving a chat_id to its project entry, returning None for unregistered
      chat_ids
  (c) loading bot-pool tokens from environment variables
      (TELEGRAM_BOT_TOKEN_DISPATCHER / _T1 / _T2) -- missing T1/T2 are tolerated
      since pool bots are optional
  (d) the registry re-reads from disk on each call (no stale import-time cache)

All tests mock the filesystem and environment — no real config or tokens are used.

Note: unlike test_bot_config.py, these tests patch the PROJECTS_CONFIG_PATH module
attribute directly (without reloading the module). load_projects()/get_bot_tokens()
look up that attribute / os.environ at call time, so a plain attribute patch is
sufficient and avoids the reload-discards-patch pitfall (a fresh module reload would
recompute PROJECTS_CONFIG_PATH from scratch, discarding the patched value).

Tests are intentionally written before the implementation so they fail on first run.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import windows.src.project_registry as registry_mod


VALID_REGISTRY = {
    "_comment": "Maps Telegram chat IDs to project instances for the bot pool.",
    "projects": {
        "research-agent": {
            "chat_id": 111222333,
            "state_dir": "/mnt/d/research-agent/state",
            "project_dir": "/mnt/d/research-agent",
            "dispatcher_prompt": "/mnt/d/research-agent/shared/prompts/dispatcher_system.md",
        },
        "second-project": {
            "chat_id": 444555666,
            "state_dir": "/mnt/d/second-project/state",
            "project_dir": "/mnt/d/second-project",
            "dispatcher_prompt": "/mnt/d/second-project/shared/prompts/dispatcher_system.md",
        },
    },
}


def _write_registry(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# (a) loading projects.json
# ---------------------------------------------------------------------------

def test_load_projects_returns_registered_projects(tmp_path):
    registry_file = tmp_path / "projects.json"
    _write_registry(registry_file, VALID_REGISTRY)

    with patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        registry = registry_mod.load_projects()

    assert "research-agent" in registry["projects"]
    assert registry["projects"]["research-agent"]["chat_id"] == 111222333
    assert registry["projects"]["research-agent"]["state_dir"] == "/mnt/d/research-agent/state"


def test_load_projects_missing_file_returns_empty_registry(tmp_path):
    missing = tmp_path / "does_not_exist.json"

    with patch.object(registry_mod, "PROJECTS_CONFIG_PATH", missing):
        registry = registry_mod.load_projects()

    assert registry == {"projects": {}}


# ---------------------------------------------------------------------------
# (b) resolving a chat_id to a project entry
# ---------------------------------------------------------------------------

def test_resolve_project_returns_entry_for_known_chat_id(tmp_path):
    registry_file = tmp_path / "projects.json"
    _write_registry(registry_file, VALID_REGISTRY)

    with patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        entry = registry_mod.resolve_project(444555666)

    assert entry is not None
    assert entry["project_id"] == "second-project"
    assert entry["state_dir"] == "/mnt/d/second-project/state"
    assert entry["project_dir"] == "/mnt/d/second-project"
    assert entry["dispatcher_prompt"] == "/mnt/d/second-project/shared/prompts/dispatcher_system.md"


def test_resolve_project_returns_none_for_unregistered_chat_id(tmp_path):
    registry_file = tmp_path / "projects.json"
    _write_registry(registry_file, VALID_REGISTRY)

    with patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        entry = registry_mod.resolve_project(999999999)

    assert entry is None


def test_resolve_project_returns_none_when_registry_missing(tmp_path):
    missing = tmp_path / "does_not_exist.json"

    with patch.object(registry_mod, "PROJECTS_CONFIG_PATH", missing):
        entry = registry_mod.resolve_project(111222333)

    assert entry is None


# ---------------------------------------------------------------------------
# (c) bot-pool tokens from environment variables
# ---------------------------------------------------------------------------

def test_get_bot_tokens_reads_all_configured_env_vars():
    env = {
        "TELEGRAM_BOT_TOKEN_DISPATCHER": "dispatcher-token",
        "TELEGRAM_BOT_TOKEN_T1": "t1-token",
        "TELEGRAM_BOT_TOKEN_T2": "t2-token",
    }
    with patch.dict(os.environ, env, clear=False):
        tokens = registry_mod.get_bot_tokens()

    assert tokens["dispatcher"] == "dispatcher-token"
    assert tokens["T1"] == "t1-token"
    assert tokens["T2"] == "t2-token"


def test_get_bot_tokens_tolerates_missing_pool_tokens():
    env = {"TELEGRAM_BOT_TOKEN_DISPATCHER": "dispatcher-token"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("TELEGRAM_BOT_TOKEN_T1", None)
        os.environ.pop("TELEGRAM_BOT_TOKEN_T2", None)

        tokens = registry_mod.get_bot_tokens()

    assert tokens["dispatcher"] == "dispatcher-token"
    assert tokens["T1"] is None
    assert tokens["T2"] is None


def test_get_bot_tokens_tolerates_missing_dispatcher_token():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TELEGRAM_BOT_TOKEN_DISPATCHER", None)
        os.environ.pop("TELEGRAM_BOT_TOKEN_T1", None)
        os.environ.pop("TELEGRAM_BOT_TOKEN_T2", None)

        tokens = registry_mod.get_bot_tokens()

    assert tokens == {"dispatcher": None, "T1": None, "T2": None}


# ---------------------------------------------------------------------------
# (d) load_projects() re-reads from disk on each call (no stale cache)
# ---------------------------------------------------------------------------

def test_load_projects_rereads_on_each_call(tmp_path):
    registry_file = tmp_path / "projects.json"

    registry_v1 = {"projects": {"proj-a": {"chat_id": 1, "state_dir": "a", "project_dir": "a", "dispatcher_prompt": "a"}}}
    _write_registry(registry_file, registry_v1)

    with patch.object(registry_mod, "PROJECTS_CONFIG_PATH", registry_file):
        first = registry_mod.load_projects()

        assert "proj-a" in first["projects"]
        assert "proj-b" not in first["projects"]

        registry_v2 = {
            "projects": {
                "proj-a": {"chat_id": 1, "state_dir": "a", "project_dir": "a", "dispatcher_prompt": "a"},
                "proj-b": {"chat_id": 2, "state_dir": "b", "project_dir": "b", "dispatcher_prompt": "b"},
            }
        }
        _write_registry(registry_file, registry_v2)

        second = registry_mod.load_projects()

    assert "proj-b" in second["projects"], (
        "load_projects() must re-read from disk on each call — stale in-memory cache detected"
    )
