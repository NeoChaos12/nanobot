"""
TDD tests for windows/src/bot_config.py.

These tests define the required behaviour of the framework's bot_config module:
  (a) valid config loads all required fields
  (b) missing TELEGRAM_TOKEN raises ValueError
  (c) missing config file raises FileNotFoundError
  (d) _cfg() re-reads from disk on each call (no stale import-time cache)

All tests mock the filesystem — no real config file is used.
Tests are intentionally written before the implementation so they fail on first run.
"""

import json
import sys
import importlib
from pathlib import Path
from unittest.mock import patch


VALID_CONFIG = {
    "channels": {
        "telegram": {
            "token": "123456:ABC-test-token",
            "allowed_chat_ids": [111222333],
        }
    },
    "claude": {
        "model": "claude-sonnet-4-6",
    },
}

CONFIG_WITHOUT_TOKEN = {
    "channels": {
        "telegram": {}
    }
}


def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _reload_module():
    """Re-import bot_config so module-level code re-runs with the current patch."""
    mod_name = "windows.src.bot_config"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    # Also remove parent packages if cached without the child
    for parent in ["windows.src", "windows"]:
        if parent in sys.modules:
            del sys.modules[parent]
    import windows.src.bot_config as m
    return m


# ---------------------------------------------------------------------------
# (a) valid config loads all required fields
# ---------------------------------------------------------------------------

def test_load_config_returns_required_fields(tmp_path):
    cfg_file = tmp_path / "nanobot.config.json"
    _write_config(cfg_file, VALID_CONFIG)

    with patch("windows.src.bot_config.CONFIG_PATH", cfg_file):
        mod = _reload_module()
        cfg = mod.load_config()

    assert "channels" in cfg
    assert "telegram" in cfg["channels"]
    assert cfg["channels"]["telegram"]["token"] == "123456:ABC-test-token"


def test_telegram_token_attribute_set_on_import(tmp_path):
    cfg_file = tmp_path / "nanobot.config.json"
    _write_config(cfg_file, VALID_CONFIG)

    with patch("windows.src.bot_config.CONFIG_PATH", cfg_file):
        mod = _reload_module()

    assert mod.TELEGRAM_TOKEN == "123456:ABC-test-token"


# ---------------------------------------------------------------------------
# (b) missing TELEGRAM_TOKEN raises ValueError
# ---------------------------------------------------------------------------

def test_missing_telegram_token_raises_value_error(tmp_path):
    cfg_file = tmp_path / "nanobot.config.json"
    _write_config(cfg_file, CONFIG_WITHOUT_TOKEN)

    import pytest
    with pytest.raises(ValueError, match="TELEGRAM_TOKEN"):
        with patch("windows.src.bot_config.CONFIG_PATH", cfg_file):
            _reload_module()


# ---------------------------------------------------------------------------
# (c) missing file raises FileNotFoundError
# ---------------------------------------------------------------------------

def test_missing_config_file_raises_file_not_found(tmp_path):
    missing = tmp_path / "does_not_exist.json"

    import pytest
    with pytest.raises(FileNotFoundError):
        with patch("windows.src.bot_config.CONFIG_PATH", missing):
            _reload_module()


# ---------------------------------------------------------------------------
# (d) _cfg() re-reads from disk on each call (no stale cache)
# ---------------------------------------------------------------------------

def test_cfg_rereads_on_each_call(tmp_path):
    cfg_file = tmp_path / "nanobot.config.json"

    # First read — original token
    config_v1 = {**VALID_CONFIG, "version": 1}
    config_v1["channels"]["telegram"]["token"] = "token-v1"
    _write_config(cfg_file, config_v1)

    with patch("windows.src.bot_config.CONFIG_PATH", cfg_file):
        mod = _reload_module()
        first = mod._cfg()

    assert first["channels"]["telegram"]["token"] == "token-v1"

    # Update the file on disk
    config_v2 = json.loads(cfg_file.read_text())
    config_v2["channels"]["telegram"]["token"] = "token-v2"
    _write_config(cfg_file, config_v2)

    # Second call should reflect the updated file
    with patch("windows.src.bot_config.CONFIG_PATH", cfg_file):
        second = mod._cfg()

    assert second["channels"]["telegram"]["token"] == "token-v2", (
        "_cfg() must re-read from disk on each call — stale in-memory cache detected"
    )
