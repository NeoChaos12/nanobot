#!/usr/bin/env python3
"""
notify.py — Send a Telegram message from any agent layer (WSL or Windows).

Usage (CLI):
    python notify.py "Your message here"
    python notify.py "Your message here" --chat-id 123456789

Usage (import):
    from notify import send_notification
    send_notification("Task complete: 5 results found for target X")

All messages are sent with parse_mode=HTML. Use Telegram HTML tags:
    <b>bold</b>  <i>italic</i>  <code>inline code</code>  <pre>block</pre>

Reads bot token and default chat_id from:
    shared/config/nanobot.config.json  (relative to this file)
"""

import json
import sys
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config" / "nanobot.config.json"


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def send_notification(
    message: str,
    chat_id: int | None = None,
    parse_mode: str = "HTML",
) -> bool:
    """
    Send a Telegram message. Returns True on success, False on failure.
    Falls back to printing to stdout if config is unavailable.
    """
    try:
        config = _load_config()
        token = config["channels"]["telegram"]["token"]
        if chat_id is None:
            ids = config.get("allowed_chat_ids", [])
            if not ids:
                print(f"[notify] No chat_id configured. Message: {message}", file=sys.stderr)
                return False
            chat_id = ids[0]  # default to first allowed chat

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            "chat_id":    chat_id,
            "text":       message,
            "parse_mode": parse_mode,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200

    except Exception as exc:
        print(f"[notify] Failed to send message: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Send a Telegram notification")
    parser.add_argument("message", help="Message text (HTML tags supported)")
    parser.add_argument("--chat-id", type=int, default=None, help="Override chat_id")
    args = parser.parse_args()
    ok = send_notification(args.message, chat_id=args.chat_id)
    sys.exit(0 if ok else 1)
