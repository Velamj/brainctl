"""Trust scoring primitives shared across the MCP and CLI surfaces.

This module is the single source of truth for trust-update logic. It is
intentionally thin: it accepts a caller-owned ``sqlite3.Connection`` so
that connection lifecycle (open/close, isolation, transactions) stays the
responsibility of the caller. The MCP path opens via
``mcp_tools_trust._db()`` and closes in a ``finally``; the CLI path uses
``_impl.get_db()`` and follows CLI conventions.

Why a shared module? Trust contradiction logic was previously implemented
in two parallel copies (``mcp_tools_trust.tool_trust_update_contradiction``
and ``_impl.cmd_trust_update_contradiction``). Bug 7 in the 2.2.0 audit
had to be patched in both places; the second patch (commit c1ae73a) was a
follow-up to the first. Keeping two copies is a drift bomb — anyone
fixing the next trust bug in one copy will likely miss the other.

Both surfaces now delegate to ``apply_contradiction_penalty`` here. Keep
the call sites thin. If you change behavior, change it here.
"""
from __future__ import annotations

import sqlite3

from agentmemory.lib.mcp_helpers import rows_to_list


# Penalty deltas — see apply_contradiction_penalty docstring for AGM rationale.
_LOSER_DELTA_UNRESOLVED: float = -0.20
_LOSER_DELTA_RESOLVED: float = -0.05
_WINNER_DELTA_RESOLVED: float = 0.02

# Trust score floor/ceiling — clamps mirror the SQL clamp in _apply().
_TRUST_FLOOR: float = 0.30
_TRUST_CEILING: float = 1.0


def apply_contradiction_penalty(
    db: sqlite3.Connection,
    memory_id_a: int,
    memory_id_b: int,
    resolved: bool = False,
) -> dict:
    """Penalize trust scores on contradicting memories — AGM-correct (loser-by-trust).

    Bug 7 fix (2.2.0): the prior implementations penalized memories by
    *argument order*, not by who lost the contradiction. AGM prescribes
    that the lower-trust side absorbs the larger penalty (it lost), while
    the higher-trust side either holds steady (unresolved — conflict
    still live) or earns a small reinforcement (resolved — its
    credibility was vindicated).

    Penalty schedule:
      - ``resolved=False`` (still in conflict):   loser -0.20, winner unchanged.
      - ``resolved=True``  (post-resolution):     loser -0.05, winner +0.02.

    Tie-breaker: on exact trust equality, penalize both equally with the
    same delta (-0.20 unresolved, -0.05 resolved). Entrenchment-by-age is
    intentionally *not* used here — temporal entrenchment is the PII
    gate's responsibility, not trust's.

    Floor: trust_score is clamped to ``[0.30, 1.0]`` (in SQL, via the
    same ROUND/MIN/MAX wrapper that the original implementations used).

    The caller owns the ``db`` connection: this function does not open
    or close it, and does not roll back on failure. It does call
    ``db.commit()`` on success so the caller does not have to. Unexpected
    exceptions (DB errors, etc.) propagate to the caller — only the
    "memories not found" validation produces an ``{"ok": False, ...}``
    result.

    Returns a dict with the same keys both pre-refactor surfaces
    exposed:

        {
            "ok": bool,
            "resolved": bool,
            "loser_id": int | None,        # None on tie
            "winner_id": int | None,       # None on tie
            "tie": bool,
            "updated_memories": list[dict] # post-update rows
        }

    On validation failure (one or both IDs missing):

        {"ok": False, "error": "Both memories must exist; found N of 2 (ids: A, B)"}
    """
    rows = db.execute(
        "SELECT id, trust_score FROM memories WHERE id IN (?, ?)",
        (memory_id_a, memory_id_b),
    ).fetchall()
    if len(rows) != 2:
        return {
            "ok": False,
            "error": (
                f"Both memories must exist; found {len(rows)} of 2 "
                f"(ids: {memory_id_a}, {memory_id_b})"
            ),
        }

    scores = {int(r["id"]): float(r["trust_score"] or 1.0) for r in rows}
    trust_a = scores[memory_id_a]
    trust_b = scores[memory_id_b]

    # Loser = lower-trust side. On exact tie, treat both as losers.
    tie = (trust_a == trust_b)
    if tie:
        loser_id, winner_id = None, None
    elif trust_a < trust_b:
        loser_id, winner_id = memory_id_a, memory_id_b
    else:
        loser_id, winner_id = memory_id_b, memory_id_a

    loser_delta = _LOSER_DELTA_RESOLVED if resolved else _LOSER_DELTA_UNRESOLVED
    # Winner reinforcement only when the contradiction is settled —
    # premature reinforcement during a live conflict would mis-credit a
    # still-disputed memory.
    winner_delta = _WINNER_DELTA_RESOLVED if resolved else 0.0

    def _apply(mem_id: int, delta: float) -> None:
        db.execute(
            "UPDATE memories SET "
            "trust_score = ROUND(MIN(1.0, MAX(0.30, trust_score + ?)), 4), "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%S','now') "
            "WHERE id = ?",
            (delta, mem_id),
        )

    if tie:
        # Symmetric tie: both sides eat the loser-side delta.
        _apply(memory_id_a, loser_delta)
        _apply(memory_id_b, loser_delta)
    else:
        _apply(loser_id, loser_delta)
        if winner_delta != 0.0:
            _apply(winner_id, winner_delta)

    out_rows = db.execute(
        "SELECT id, trust_score FROM memories WHERE id IN (?, ?)",
        (memory_id_a, memory_id_b),
    ).fetchall()
    db.commit()
    return {
        "ok": True,
        "resolved": resolved,
        "loser_id": loser_id,
        "winner_id": winner_id,
        "tie": tie,
        "updated_memories": rows_to_list(out_rows),
    }
