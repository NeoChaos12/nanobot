"""
Mutable shared state for the bot process.

All consumers must import this module and access attributes as bot_state.X,
never as `from bot_state import X` — that would capture the value at import
time and miss later mutations.
"""

from datetime import datetime
from typing import Optional
import asyncio

# Per-chat session state: { chat_id: { "session_id": str|None, "idle_task": asyncio.Task|None } }
sessions: dict[int, dict] = {}

# Chats that sent one /interrupt and are waiting for phase-2 confirmation
interrupt_pending: set[int] = set()

# Keepalive background loop state (set during _post_init)
keepalive_paused:       bool               = False
keepalive_last_ok:      bool               = True
keepalive_last_ping_at: Optional[datetime] = None
keepalive_next_ping_at: Optional[datetime] = None
keepalive_resume_event: Optional[asyncio.Event] = None  # set in _post_init
