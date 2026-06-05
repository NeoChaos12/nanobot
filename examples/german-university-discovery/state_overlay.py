"""
state_overlay.py — Domain-specific state extensions for the german-university-discovery example.

Drop this file alongside the framework's windows/src/state.py and import it in place of
(or in addition to) the base module. It adds two domain entities — institutions and groups —
and wires the compact_snapshot domain_fn so the dispatcher sees entity counts.

Register the interrupt callback at startup:

    from windows.src.interrupt import register_domain_interrupt
    from state_overlay import mark_in_progress_as_interrupted
    register_domain_interrupt(mark_in_progress_as_interrupted)

Project entities:
  institutions.json  — one entry per institution, status: pending | in_progress | needs_review | approved | interrupted
  groups.json        — one entry per research group, status: pending | in_progress | done | error
"""

from windows.src.state import (
    STATE_DIR,
    atomic_write,
    read_json,
    compact_snapshot as _base_compact_snapshot,
)

# ---------------------------------------------------------------------------
# Domain file constants
# ---------------------------------------------------------------------------

INSTITUTIONS_FILE = STATE_DIR / "institutions.json"
GROUPS_FILE       = STATE_DIR / "groups.json"


# ---------------------------------------------------------------------------
# Typed accessors
# ---------------------------------------------------------------------------

def read_institutions() -> list[dict]:
    return read_json(INSTITUTIONS_FILE)

def write_institutions(data: list[dict]) -> None:
    atomic_write(INSTITUTIONS_FILE, data)

def read_groups() -> list[dict]:
    return read_json(GROUPS_FILE)

def write_groups(data: list[dict]) -> None:
    atomic_write(GROUPS_FILE, data)


# ---------------------------------------------------------------------------
# Interrupt helper — register with interrupt.register_domain_interrupt()
# ---------------------------------------------------------------------------

def mark_in_progress_as_interrupted() -> list[str]:
    """
    Mark all in-progress institutions and groups as interrupted.
    Returns IDs of affected institutions.
    Call this before sending SIGINT to any running agent process.
    """
    affected_ids: list[str] = []

    institutions = read_institutions()
    for inst in institutions:
        if inst.get("status") == "in_progress":
            inst["status"] = "interrupted"
            affected_ids.append(inst["id"])
    if affected_ids:
        write_institutions(institutions)

    groups = read_groups()
    changed = False
    for g in groups:
        if g.get("status") == "in_progress":
            g["status"] = "interrupted"
            changed = True
    if changed:
        write_groups(groups)

    return affected_ids


# ---------------------------------------------------------------------------
# Snapshot with domain counts
# ---------------------------------------------------------------------------

def _domain_summary() -> str:
    institutions = read_institutions()
    groups = read_groups()

    def count_by_status(items: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            s = item.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return counts

    ic = count_by_status(institutions)
    gc = count_by_status(groups)

    inst_s = ", ".join(f"{v} {k}" for k, v in ic.items()) or "none"
    grp_s  = ", ".join(f"{v} {k}" for k, v in gc.items()) or "none"

    return (
        f"Institutions: {len(institutions)} total ({inst_s})\n"
        f"Groups:       {len(groups)} total ({grp_s})\n"
    )


def compact_snapshot() -> str:
    """Full snapshot: domain entity counts + framework-level sections."""
    return _base_compact_snapshot(domain_fn=_domain_summary)
