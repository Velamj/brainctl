"""Tests for Task 1: Belief Collapse Mechanics.

Covers:
- test_collapse_resolves_to_single_state
- test_collapse_logs_event
- test_non_superposed_is_noop
- test_collapse_preserves_amplitude
- test_time_trigger_catches_old_beliefs
- test_recent_beliefs_not_triggered
"""
from __future__ import annotations

import math
import sqlite3
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _collapse_belief, _check_collapse_triggers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_db() -> sqlite3.Connection:
    """Create a minimal in-memory DB with the tables needed for collapse tests."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE agents (
            id   TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );

        CREATE TABLE agent_beliefs (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id                TEXT    NOT NULL REFERENCES agents(id),
            topic                   TEXT    NOT NULL,
            belief_content          TEXT    NOT NULL,
            confidence              REAL    NOT NULL DEFAULT 1.0,
            source_memory_id        INTEGER,
            source_event_id         INTEGER,
            is_assumption           INTEGER NOT NULL DEFAULT 0,
            last_updated_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            invalidated_at          TEXT,
            invalidation_reason     TEXT,
            created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            is_superposed           INTEGER DEFAULT 0,
            belief_density_matrix   BLOB    DEFAULT NULL,
            coherence_score         REAL    DEFAULT 0.0,
            entanglement_source_ids TEXT    DEFAULT NULL
        );

        CREATE TABLE belief_collapse_events (
            id               TEXT    PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            belief_id        TEXT    NOT NULL,
            agent_id         TEXT    NOT NULL,
            collapsed_state  TEXT    NOT NULL,
            measured_amplitude REAL  NOT NULL,
            collapse_type    TEXT    NOT NULL CHECK (collapse_type IN ('query', 'action', 'update')),
            collapse_context TEXT    DEFAULT NULL,
            collapse_fidelity REAL   DEFAULT 1.0,
            created_at       TEXT    DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute("INSERT INTO agents (id, name) VALUES ('agent-test', 'Test Agent')")
    db.commit()
    return db


def insert_belief(db, *, topic="sky_color", content="blue", confidence=0.8,
                  is_superposed=1, days_old=0) -> int:
    """Insert a belief and return its id."""
    if days_old > 0:
        created = f"strftime('%Y-%m-%dT%H:%M:%S', 'now', '-{days_old} days')"
        sql = (
            f"INSERT INTO agent_beliefs (agent_id, topic, belief_content, confidence, "
            f"is_superposed, created_at) "
            f"VALUES ('agent-test', ?, ?, ?, ?, {created})"
        )
        cur = db.execute(sql, (topic, content, confidence, is_superposed))
    else:
        cur = db.execute(
            "INSERT INTO agent_beliefs "
            "(agent_id, topic, belief_content, confidence, is_superposed) "
            "VALUES ('agent-test', ?, ?, ?, ?)",
            (topic, content, confidence, is_superposed),
        )
    db.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCollapseResolves:
    """_collapse_belief resolves superposed belief to a single definite state."""

    def test_collapse_resolves_to_single_state(self):
        db = make_db()
        bid = insert_belief(db, topic="weather", content="sunny|cloudy", confidence=0.9,
                            is_superposed=1)
        result = _collapse_belief(db, bid, "agent-test", "query", "sunny")
        assert result["collapsed_to"] == "sunny"
        row = db.execute(
            "SELECT belief_content, is_superposed FROM agent_beliefs WHERE id = ?", (bid,)
        ).fetchone()
        assert row["belief_content"] == "sunny"
        assert row["is_superposed"] == 0

    def test_collapse_logs_event(self):
        db = make_db()
        bid = insert_belief(db, topic="temperature", content="hot|cold", confidence=0.64,
                            is_superposed=1)
        _collapse_belief(db, bid, "agent-test", "action", "cold")
        events = db.execute(
            "SELECT * FROM belief_collapse_events WHERE belief_id = ?", (str(bid),)
        ).fetchall()
        assert len(events) == 1
        ev = events[0]
        assert ev["collapsed_state"] == "cold"
        assert ev["collapse_type"] == "action"
        assert ev["agent_id"] == "agent-test"

    def test_non_superposed_is_noop(self):
        """Collapsing an already-definite belief returns already_collapsed=True."""
        db = make_db()
        bid = insert_belief(db, topic="color", content="green", confidence=0.7,
                            is_superposed=0)
        result = _collapse_belief(db, bid, "agent-test", "update", "red")
        assert result.get("already_collapsed") is True
        assert result.get("current_value") == "green"
        # Belief unchanged
        row = db.execute(
            "SELECT belief_content FROM agent_beliefs WHERE id = ?", (bid,)
        ).fetchone()
        assert row["belief_content"] == "green"
        # No events logged
        count = db.execute(
            "SELECT COUNT(*) FROM belief_collapse_events WHERE belief_id = ?", (str(bid),)
        ).fetchone()[0]
        assert count == 0

    def test_collapse_preserves_amplitude(self):
        """measured_amplitude stored equals sqrt(confidence)."""
        db = make_db()
        confidence = 0.49
        bid = insert_belief(db, topic="phase", content="a|b", confidence=confidence,
                            is_superposed=1)
        result = _collapse_belief(db, bid, "agent-test", "query", "a")
        expected_amp = math.sqrt(confidence)
        assert abs(result["amplitude"] - expected_amp) < 1e-9
        # Also verify it was stored in the DB
        ev = db.execute(
            "SELECT measured_amplitude FROM belief_collapse_events WHERE belief_id = ?",
            (str(bid),),
        ).fetchone()
        assert abs(ev["measured_amplitude"] - expected_amp) < 1e-9


class TestCheckCollapseTriggers:
    """_check_collapse_triggers finds stale superposed beliefs."""

    def test_time_trigger_catches_old_beliefs(self):
        """Beliefs older than max_superposition_days are returned."""
        db = make_db()
        insert_belief(db, topic="old-topic", content="x|y", confidence=0.5,
                      is_superposed=1, days_old=35)
        candidates = _check_collapse_triggers(db, "agent-test", max_superposition_days=30)
        assert len(candidates) >= 1
        topics = [c["topic"] for c in candidates]
        assert "old-topic" in topics

    def test_recent_beliefs_not_triggered(self):
        """Beliefs younger than max_superposition_days are NOT returned."""
        db = make_db()
        insert_belief(db, topic="fresh-topic", content="p|q", confidence=0.5,
                      is_superposed=1, days_old=5)
        candidates = _check_collapse_triggers(db, "agent-test", max_superposition_days=30)
        topics = [c["topic"] for c in candidates]
        assert "fresh-topic" not in topics
