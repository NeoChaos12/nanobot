"""
interrupt.py — Hard interrupt handler.

Called by listener.py when /interrupt is detected.
Operates entirely at the Python process level — no LLM involvement at any point.

Phase 1 (first /interrupt):
  1. Mark all in_progress state entries as 'interrupted' (atomic write — happens first)
  2. Send process.terminate() to all tracked WSL subprocesses
  3. Launch a background monitor task that reports status every 60s
  4. Return immediately so the listener stays responsive

Phase 2 (second /interrupt, or explicit force=True):
  1. Cancel the monitor task
  2. Send process.kill() to any remaining processes
  3. Confirm to the user

Safety note:
  State consistency is guaranteed by atomic writes in state.py, not by Claude Code's
  shutdown behaviour (which is unreliable — see known WSL/SIGTERM issues).
  The active_jobs dict is imported directly from dispatcher to share the same reference.
"""

import asyncio
import logging
from typing import Callable, Awaitable

from windows.src.dispatcher import active_jobs

logger = logging.getLogger(__name__)

MONITOR_INTERVAL_SECONDS = 60

# Background monitor task reference — allows cancellation on second interrupt
_monitor_task: asyncio.Task | None = None

# Optional domain callback: marks domain entities as interrupted before SIGTERM.
# Set once at startup via register_domain_interrupt(); returns list of affected IDs.
_domain_interrupt_fn: Callable[[], list[str]] | None = None

SendFn = Callable[[str], Awaitable[None]]


def register_domain_interrupt(fn: Callable[[], list[str]]) -> None:
    """Register a project-specific function that marks domain state as interrupted."""
    global _domain_interrupt_fn
    _domain_interrupt_fn = fn


async def handle_interrupt(send: SendFn, force: bool = False) -> None:
    """
    Entry point called by listener.py.

    `send`  — async callable that sends a message to the user's Telegram chat.
    `force` — True when this is the second /interrupt (escalate to SIGKILL).
    """
    global _monitor_task

    # --- Phase 2: force kill ---
    if force:
        if _monitor_task and not _monitor_task.done():
            _monitor_task.cancel()
            _monitor_task = None
        await _force_kill(send)
        return

    # --- No active jobs ---
    if not active_jobs:
        await send("No active jobs running.")
        return

    # --- Phase 1: graceful termination ---

    # Step 1: update state BEFORE signalling any process
    affected = _domain_interrupt_fn() if _domain_interrupt_fn else []
    if affected:
        await send(
            f"State updated — {len(affected)} unit(s) marked as interrupted "
            f"and queued for rerun."
        )

    # Step 2: SIGTERM all tracked WSL subprocesses
    pids = list(active_jobs.keys())
    signalled = 0
    for pid in pids:
        job = active_jobs.get(pid)
        if job:
            try:
                job["process"].terminate()
                signalled += 1
                logger.info("Sent SIGTERM to PID %d (%s)", pid, job.get("type"))
            except ProcessLookupError:
                logger.debug("PID %d already gone", pid)

    await send(
        f"Termination signal sent to {signalled} process(es). "
        f"Monitoring for clean exit — status every {MONITOR_INTERVAL_SECONDS}s.\n"
        f"Send /interrupt again to force-kill immediately."
    )

    # Step 3: launch background monitor (non-blocking)
    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()

    _monitor_task = asyncio.create_task(
        _monitor_loop(send, initial_pids=set(pids))
    )


async def _monitor_loop(send: SendFn, initial_pids: set[int]) -> None:
    """
    Background task: report every MONITOR_INTERVAL_SECONDS whether processes
    have exited. Terminates itself when all tracked processes are gone.
    """
    elapsed = 0
    try:
        while True:
            await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
            elapsed += MONITOR_INTERVAL_SECONDS

            still_running = initial_pids & set(active_jobs.keys())
            if not still_running:
                await send("All processes exited cleanly. State is consistent.")
                return

            await send(
                f"{len(still_running)} process(es) still running "
                f"({elapsed}s elapsed since interrupt).\n"
                f"Send /interrupt again to force-kill."
            )
    except asyncio.CancelledError:
        pass  # Cancelled by phase-2 interrupt — normal


async def _force_kill(send: SendFn) -> None:
    """Send SIGKILL to all remaining active jobs and clear the registry."""
    pids = list(active_jobs.keys())
    killed = 0
    for pid in pids:
        job = active_jobs.get(pid)
        if job:
            try:
                job["process"].kill()
                killed += 1
                logger.info("Sent SIGKILL to PID %d", pid)
            except ProcessLookupError:
                logger.debug("PID %d already gone", pid)
    active_jobs.clear()

    if killed:
        await send(
            f"Force-killed {killed} process(es). "
            f"State is consistent — interrupted entries will rerun on next sweep."
        )
    else:
        await send("No processes remained to kill. State is consistent.")
