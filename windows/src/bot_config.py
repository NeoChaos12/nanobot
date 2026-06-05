import json
from pathlib import Path

BASE        = Path(__file__).parent.parent.parent
CONFIG_PATH = BASE / "shared" / "config" / "nanobot.config.json"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_telegram_token(cfg: dict) -> str:
    try:
        token = cfg["channels"]["telegram"]["token"]
    except KeyError:
        token = None
    if not token:
        raise ValueError("TELEGRAM_TOKEN is missing from config (channels.telegram.token)")
    return token


_initial_cfg = load_config()
TELEGRAM_TOKEN: str = _get_telegram_token(_initial_cfg)


def _cfg() -> dict:
    """Return config re-read from disk on each call."""
    return load_config()


def _reload_config() -> dict:
    """Re-read nanobot.config.json and return the new config."""
    return load_config()
