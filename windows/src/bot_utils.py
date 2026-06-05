from tzlocal import get_localzone

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from windows.src.bot_config import _cfg

USER_TZ = get_localzone()


def _is_allowed(chat_id: int) -> bool:
    allowed = set(_cfg().get("allowed_chat_ids", []))
    if not allowed:
        return True
    return chat_id in allowed


async def _send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    """Send a message with HTML parse mode, splitting at Telegram's 4096-char limit."""
    for i in range(0, max(len(text), 1), 4096):
        await context.bot.send_message(
            chat_id=chat_id,
            text=text[i:i + 4096],
            parse_mode=ParseMode.HTML,
        )
