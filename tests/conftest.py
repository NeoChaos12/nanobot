"""
Shared pytest fixtures for framework-repo tests.

Creates a minimal default config so that windows.src.bot_config can be imported by
unittest.mock.patch without a real project config file present.

Also wraps test_bot_config._reload_module so that any CONFIG_PATH patch applied before
the reload is re-applied to the freshly imported module.  This is necessary because
_reload_module deletes and re-imports the module, which resets module-level globals,
making unittest.mock.patch ineffective without the re-apply step.
"""

import json
import sys
from pathlib import Path

import pytest

_DEFAULT_CONFIG = {
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

_FRAMEWORK_ROOT = Path(__file__).parent.parent
_DEFAULT_CONFIG_PATH = _FRAMEWORK_ROOT / "shared" / "config" / "nanobot.config.json"


@pytest.fixture(autouse=True, scope="session")
def default_config_file():
    """Create a minimal default config at the module's expected default path."""
    _DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DEFAULT_CONFIG_PATH.write_text(json.dumps(_DEFAULT_CONFIG), encoding="utf-8")
    yield _DEFAULT_CONFIG_PATH


def _get_test_bot_config_module():
    """Return the test_bot_config module as loaded by pytest (may lack package prefix)."""
    for key in ("test_bot_config", "tests.test_bot_config"):
        if key in sys.modules:
            return sys.modules[key]
    return None


@pytest.fixture(autouse=True)
def propagate_config_path_after_reload(monkeypatch):
    """
    Wrap _reload_module in test_bot_config so CONFIG_PATH patches survive the fresh import.

    unittest.mock.patch sets CONFIG_PATH on the *existing* module object.
    _reload_module() then deletes that object and creates a new one, so the patch
    is lost.  This wrapper:
      1. Captures CONFIG_PATH from the currently-patched module before reload.
      2. Runs the original _reload_module().
      3. If the captured path differs from the new module's default path, re-applies
         it and re-runs the token-extraction / config-load logic so that TELEGRAM_TOKEN
         and any errors propagate correctly.
    """
    test_mod = _get_test_bot_config_module()
    if test_mod is None:
        yield
        return

    original_reload = test_mod._reload_module

    def _patched_reload():
        # Capture the (possibly patched) CONFIG_PATH before module deletion.
        current_mod = sys.modules.get("windows.src.bot_config")
        config_path_before = (
            getattr(current_mod, "CONFIG_PATH", None) if current_mod else None
        )

        # Run original: deletes windows*, re-imports with default path.
        new_mod = original_reload()

        # Re-apply patch if CONFIG_PATH was changed from the default.
        if config_path_before is not None and config_path_before != new_mod.CONFIG_PATH:
            new_mod.CONFIG_PATH = config_path_before
            # Re-run initialization with the patched path.
            # Let FileNotFoundError / ValueError propagate for error-path tests.
            cfg = new_mod.load_config()
            new_mod.TELEGRAM_TOKEN = new_mod._get_telegram_token(cfg)

        return new_mod

    monkeypatch.setattr(test_mod, "_reload_module", _patched_reload)
    yield
