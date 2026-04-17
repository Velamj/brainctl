"""Tests for hippocampus.resolve_contradictions — Bug 8 (2.2.0): symmetric EWC check.

Pre-fix behavior: only the loser-by-confidence's ewc_importance was inspected
before retirement. A high-EWC memory that happened to win the confidence
comparison was never checked, so its low-EWC partner could be silently retired.

Post-fix behavior: BOTH sides' ewc_importance are checked before retirement.
If either is > 0.7, text similarity must exceed 0.9 — otherwise the pair is
flagged with a 'EWC-protected contradiction queued for review' warning event
and neither memory is retired.
"""
from __future__ import annotations

import sqlite3

import pytest

from agentmemory.hippocampus import resolve_contradictions


def _make_db() -> sqlite3.Connection:
    """In-memory DB with the minimum columns resolve_contradictions touches."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL DEFAULT 'test',
            content TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'lesson',
            scope TEXT NOT NULL DEFAULT 'global',
            confidence REAL NOT NULL DEFAULT 0.5,
            alpha REAL DEFAULT 1.0,
            beta REAL DEFAULT 1.0,
            recalled_count INTEGER DEFAULT 0,
            temporal_class TEXT DEFAULT 'medium',
            ewc_importance REAL DEFAULT 0.0,
            retired_at TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL DEFAULT 'test',
            event_type TEXT NOT NULL DEFAULT 'observation',
            summary TEXT NOT NULL,
            detail TEXT,
            importance REAL DEFAULT 0.5,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        """
    )
    return db


def _insert(
    db: sqlite3.Connection,
    content: str,
    confidence: float,
    ewc_importance: float = 0.0,
    category: str = "lesson",
    scope: str = "global",
    temporal_class: str = "medium",
    agent_id: str = "test",
) -> int:
    cur = db.execute(
        "INSERT INTO memories (agent_id, content, category, scope, confidence, "
        "ewc_importance, temporal_class) VALUES (?,?,?,?,?,?,?)",
        (agent_id, content, category, scope, confidence, ewc_importance, temporal_class),
    )
    db.commit()
    return cur.lastrowid


def _is_retired(db: sqlite3.Connection, mem_id: int) -> bool:
    row = db.execute("SELECT retired_at FROM memories WHERE id = ?", (mem_id,)).fetchone()
    return row is not None and row["retired_at"] is not None


def _warning_count(db: sqlite3.Connection) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM events WHERE event_type='warning' "
        "AND summary LIKE '%EWC-protected%'"
    ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# Baseline: contradictions without EWC protection still retire the loser
# ---------------------------------------------------------------------------

# Test text pairs are intentionally long enough that the SequenceMatcher
# ratio sits well below the 0.9 EWC-protection bar (so protection actually
# triggers) while still passing _are_contradictory() via the negation pairs.

_PAIR_AUTH = (
    "Auth flow supports password login only for legacy enterprise clients in region eu-west-1",
    "Auth flow does not support password login because oidc-only mandate (cors policy enforced)",
)
_PAIR_QUEUE = (
    "Queue worker for tenant ingest pipeline is enabled across all production regions",
    "Queue worker for tenant ingest pipeline is disabled until further notice from sre team",
)
_PAIR_REPL = (
    "Replication topology is enabled for the analytics cluster as of last quarter migration",
    "Replication topology is disabled for analytics cluster pending compliance review",
)
_PAIR_BACKUPS = (
    "Backups are enabled for the prod cluster nightly via the snapshot scheduler with cross-region copy",
    "Backups are disabled for prod cluster while we evaluate the new tier-2 storage backend",
)
_PAIR_HIGH_SIM = (
    # Near-identical: only the negation flips. SequenceMatcher ratio > 0.9 so
    # EWC protection should NOT block retirement.
    "Cache eviction is enabled for prod",
    "Cache eviction is disabled for prod",
)


class TestBaseline:
    def test_lower_confidence_retired(self):
        db = _make_db()
        a, b = _PAIR_AUTH
        a_id = _insert(db, a, confidence=0.9)
        b_id = _insert(db, b, confidence=0.4)

        stats = resolve_contradictions(db)
        assert stats["contradictions_found"] >= 1
        assert stats["retired"] >= 1
        # Lower confidence (b) is the loser.
        assert _is_retired(db, b_id)
        assert not _is_retired(db, a_id)

    def test_no_contradiction_no_action(self):
        db = _make_db()
        _insert(db, "Database uses PostgreSQL", confidence=0.9)
        _insert(db, "Cache uses Redis", confidence=0.9)

        stats = resolve_contradictions(db)
        assert stats["contradictions_found"] == 0
        assert stats["retired"] == 0


# ---------------------------------------------------------------------------
# Bug 8 fix: EWC protection is symmetric (winner side is checked too)
# ---------------------------------------------------------------------------

class TestSymmetricEwcProtection:
    def test_high_ewc_winner_protects_pair(self):
        """The high-EWC memory is the WINNER (higher confidence). Pre-fix this
        was never checked, so the lower-EWC loser would be retired. Post-fix:
        either-side EWC > 0.7 + low similarity → no retirement, warning emitted.
        """
        db = _make_db()
        a, b = _PAIR_AUTH  # similarity ~0.54 — well below the 0.9 EWC bar.
        # Winner has the higher confidence + high EWC.
        winner_id = _insert(db, a, confidence=0.91, ewc_importance=0.95)
        # Loser has lower confidence + low EWC.
        loser_id = _insert(db, b, confidence=0.50, ewc_importance=0.30)

        stats = resolve_contradictions(db)
        assert stats["contradictions_found"] >= 1
        # Pre-fix: loser is retired. Post-fix: NEITHER is retired because
        # the high-EWC winner triggers symmetric protection.
        assert stats["retired"] == 0
        assert stats["skipped_ewc_protected"] >= 1
        assert stats["warnings"] >= 1
        assert not _is_retired(db, winner_id)
        assert not _is_retired(db, loser_id)

    def test_high_ewc_loser_still_protects_pair(self):
        """Original behavior preserved: loser-side EWC also still protects."""
        db = _make_db()
        a, b = _PAIR_QUEUE  # similarity ~0.66
        winner_id = _insert(db, a, confidence=0.95, ewc_importance=0.20)
        loser_id = _insert(db, b, confidence=0.40, ewc_importance=0.85)

        stats = resolve_contradictions(db)
        assert stats["contradictions_found"] >= 1
        assert stats["retired"] == 0
        assert stats["skipped_ewc_protected"] >= 1
        assert not _is_retired(db, winner_id)
        assert not _is_retired(db, loser_id)

    def test_warning_event_emitted_with_both_ewc_scores(self):
        db = _make_db()
        a, b = _PAIR_REPL  # similarity ~0.69
        _insert(db, a, confidence=0.92, ewc_importance=0.95)
        _insert(db, b, confidence=0.50, ewc_importance=0.10)

        resolve_contradictions(db)
        assert _warning_count(db) >= 1
        row = db.execute(
            "SELECT detail FROM events WHERE event_type='warning' "
            "AND summary LIKE '%EWC-protected%'"
        ).fetchone()
        assert row is not None
        detail = row["detail"]
        # Both EWC scores must be present so a human reviewer can audit.
        assert "ewc=0.950" in detail
        assert "ewc=0.100" in detail
        # Similarity score visible so reviewers know why it triggered.
        assert "similarity=" in detail

    def test_low_ewc_both_sides_no_protection(self):
        """If neither side has high EWC, the original retire-the-loser path runs."""
        db = _make_db()
        a, b = _PAIR_BACKUPS  # similarity ~0.53 — would trigger protection if EWC were high.
        winner_id = _insert(db, a, confidence=0.9, ewc_importance=0.30)
        loser_id = _insert(db, b, confidence=0.4, ewc_importance=0.30)

        stats = resolve_contradictions(db)
        assert stats["contradictions_found"] >= 1
        assert stats["retired"] >= 1
        assert stats["skipped_ewc_protected"] == 0
        assert _is_retired(db, loser_id)
        assert not _is_retired(db, winner_id)

    def test_high_similarity_overrides_protection(self):
        """When similarity > 0.9 the EWC protection allows retirement (very strong
        contradiction signal — same wording with one negation)."""
        db = _make_db()
        a, b = _PAIR_HIGH_SIM  # similarity ~0.93
        winner_id = _insert(db, a, confidence=0.95, ewc_importance=0.90)
        loser_id = _insert(db, b, confidence=0.30, ewc_importance=0.10)

        stats = resolve_contradictions(db)
        assert stats["contradictions_found"] >= 1
        # Similarity exceeds the 0.9 protection threshold so the loser is retired
        # despite the winner's high EWC.
        assert stats["skipped_ewc_protected"] == 0
        assert stats["retired"] >= 1
        assert _is_retired(db, loser_id)
        assert not _is_retired(db, winner_id)


# ---------------------------------------------------------------------------
# Stats accounting integrity
# ---------------------------------------------------------------------------

class TestStatsAccounting:
    def test_skipped_ewc_increments_warnings_counter(self):
        """Both counters should bump together for EWC-protected pairs so the
        review queue size matches the protected count."""
        db = _make_db()
        a, b = _PAIR_BACKUPS  # similarity ~0.53
        _insert(db, a, confidence=0.9, ewc_importance=0.85)
        _insert(db, b, confidence=0.4, ewc_importance=0.20)

        stats = resolve_contradictions(db)
        assert stats["skipped_ewc_protected"] == stats["warnings"]
        assert stats["warnings"] >= 1
