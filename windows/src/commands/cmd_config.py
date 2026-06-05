from telegram import Update
from telegram.ext import ContextTypes

from windows.src.bot_config import _cfg, _reload_config
from windows.src.bot_utils import _send, _is_allowed

COMMAND     = "config"
DESCRIPTION = "Show/reload hot-reloadable config: /config [reload]"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /config         — show current hot-reloadable config values
    /config reload  — reload nanobot.config.json from disk without restarting
    """
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        return

    args = context.args or []
    sub  = args[0].lower() if args else "show"

    if sub == "reload":
        old = _cfg()
        try:
            new = _reload_config()
        except Exception as exc:
            await _send(context, chat_id, f"Failed to reload config: <code>{exc}</code>")
            return

        def _pick(cfg, *keys):
            v = cfg
            for k in keys:
                if not isinstance(v, dict):
                    return None
                v = v.get(k)
            return v

        watched = [
            (("session", "idle_timeout_seconds"),           "idle_timeout_seconds",           600),
            (("session", "dispatch_timeout_seconds"),        "dispatch_timeout_seconds",        600),
            (("session", "keepalive_interval_seconds"),      "keepalive_interval_seconds",      3600),
            (("session", "scheduler_poll_interval_seconds"), "scheduler_poll_interval_seconds", 60),
            (("allowed_chat_ids",),                          "allowed_chat_ids",                []),
        ]
        changes = []
        for keys, label, default in watched:
            ov = _pick(old, *keys)
            nv = _pick(new, *keys)
            if ov is None:
                ov = default
            if nv is None:
                nv = default
            if ov != nv:
                changes.append(f"• {label}: <code>{ov}</code> → <code>{nv}</code>")

        body = "Config reloaded."
        if changes:
            body += " Changes:\n" + "\n".join(changes)
        else:
            body += " No changes detected."
        await _send(context, chat_id, body)
        return

    # Show current values
    sess  = _cfg().get("session", {})
    lines = [
        "<b>Hot-reloadable config</b>  (/config reload to apply edits)",
        f"idle_timeout_seconds:            <code>{sess.get('idle_timeout_seconds', 600)}</code>",
        f"dispatch_timeout_seconds:        <code>{sess.get('dispatch_timeout_seconds', 600)}</code>",
        f"keepalive_interval_seconds:      <code>{sess.get('keepalive_interval_seconds', 3600)}</code>",
        f"scheduler_poll_interval_seconds: <code>{sess.get('scheduler_poll_interval_seconds', 60)}</code>",
        f"allowed_chat_ids:                <code>{_cfg().get('allowed_chat_ids', [])}</code>",
        "",
        "<b>Requires restart</b>",
        "• channels.telegram.token",
    ]
    await _send(context, chat_id, "\n".join(lines))
