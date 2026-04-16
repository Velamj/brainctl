"""Tests for Encoding Affect Linkage.

Task 5: Encoding Affect Linkage (Migration 037)
Papers: Eich & Metcalfe 1989, Morici et al. 2026

Each memory row now carries encoding_affect_id — a FK to the affect_log row
that was most recent for that agent when the memory was written. The helper
_get_encoding_affect_id() looks up that ID, and _affect_distance() provides
Euclidean distance in VAD space.

Covers:
- test_memory_add_captures_encoding_affect
- test_no_affect_log_returns_none
- test_affect_distance_computation
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _get_encoding_affect_id, _affect_distance


# ---------------------------------------------------------------------------
# Minimal in-memory DB fixture
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the minimal schema needed."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE agents (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            agent_type TEXT NOT NULL DEFAULT 'assistant',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            attention_class TEXT NOT NULL DEFAULT 'ic',
            attention_budget_tier INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE affect_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            valence REAL NOT NULL DEFAULT 0.0,
            arousal REAL NOT NULL DEFAULT 0.0,
            dominance REAL NOT NULL DEFAULT 0.0,
            affect_label TEXT,
            cluster TEXT,
            functional_state TEXT,
            safety_flag TEXT,
            trigger TEXT,
            source TEXT DEFAULT 'observation',
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL REFERENCES agents(id),
            category TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'global',
            content TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            encoding_affect_id INTEGER REFERENCES affect_log(id) DEFAULT NULL
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Tests for _get_encoding_affect_id
# ---------------------------------------------------------------------------

class TestGetEncodingAffectId:
    def test_no_affect_log_returns_none(self):
        """If no affect_log entry exists for the agent, return None."""
        db = _make_db()
        db.execute("INSERT INTO agents (id, display_name) VALUES ('a1', 'Agent One')")
        db.commit()
        result = _get_encoding_affect_id(db, "a1")
        assert result is None

    def test_memory_add_captures_encoding_affect(self):
        """_get_encoding_affect_id returns the most recent affect_log ID for the agent."""
        db = _make_db()
        db.execute("INSERT INTO agents (id, display_name) VALUES ('a1', 'Agent One')")
        # Insert two affect_log entries; the second should be returned
        db.execute(
            "INSERT INTO affect_log (agent_id, valence, arousal, dominance, created_at) "
            "VALUES ('a1', 0.3, 0.5, 0.2, '2026-04-15T10:00:00')"
        )
        db.execute(
            "INSERT INTO affect_log (agent_id, valence, arousal, dominance, created_at) "
            "VALUES ('a1', 0.6, 0.7, 0.4, '2026-04-15T11:00:00')"
        )
        db.commit()

        result = _get_encoding_affect_id(db, "a1")
        # Should return the ID of the most recent row (id=2)
        assert result == 2

    def test_returns_most_recent_for_agent(self):
        """Returns the most recent affect entry only for the given agent, not other agents."""
        db = _make_db()
        db.execute("INSERT INTO agents (id, display_name) VALUES ('a1', 'Agent One')")
        db.execute("INSERT INTO agents (id, display_name) VALUES ('a2', 'Agent Two')")
        # Insert affect for a2 (id=1), then a1 (id=2)
        db.execute(
            "INSERT INTO affect_log (agent_id, valence, arousal, dominance, created_at) "
            "VALUES ('a2', 0.1, 0.1, 0.1, '2026-04-15T09:00:00')"
        )
        db.execute(
            "INSERT INTO affect_log (agent_id, valence, arousal, dominance, created_at) "
            "VALUES ('a1', 0.5, 0.6, 0.3, '2026-04-15T10:00:00')"
        )
        db.commit()

        # a1 should get id=2 (its own entry), not id=1 (a2's entry)
        result = _get_encoding_affect_id(db, "a1")
        assert result == 2

    def test_no_affect_for_other_agent_returns_none(self):
        """If affect_log only has entries for a different agent, return None for the queried agent."""
        db = _make_db()
        db.execute("INSERT INTO agents (id, display_name) VALUES ('a1', 'Agent One')")
        db.execute("INSERT INTO agents (id, display_name) VALUES ('a2', 'Agent Two')")
        db.execute(
            "INSERT INTO affect_log (agent_id, valence, arousal, dominance, created_at) "
            "VALUES ('a2', 0.1, 0.1, 0.1, '2026-04-15T09:00:00')"
        )
        db.commit()

        result = _get_encoding_affect_id(db, "a1")
        assert result is None

    def test_encoding_affect_id_stored_in_memory(self):
        """After looking up encoding affect ID, the FK value can be stored in a memory row."""
        db = _make_db()
        db.execute("INSERT INTO agents (id, display_name) VALUES ('a1', 'Agent One')")
        db.execute(
            "INSERT INTO affect_log (agent_id, valence, arousal, dominance, created_at) "
            "VALUES ('a1', 0.4, 0.6, 0.2, '2026-04-15T10:00:00')"
        )
        db.commit()

        encoding_affect_id = _get_encoding_affect_id(db, "a1")
        assert encoding_affect_id is not None

        cur = db.execute(
            "INSERT INTO memories (agent_id, category, content, encoding_affect_id) "
            "VALUES ('a1', 'lesson', 'test memory', ?)",
            (encoding_affect_id,)
        )
        mid = cur.lastrowid
        db.commit()

        row = db.execute(
            "SELECT encoding_affect_id FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        assert row["encoding_affect_id"] == encoding_affect_id


# ---------------------------------------------------------------------------
# Tests for _affect_distance
# ---------------------------------------------------------------------------

class TestAffectDistance:
    def test_same_point_zero(self):
        """Distance from a point to itself is 0.0."""
        d = _affect_distance(0.5, 0.7, 0.3, 0.5, 0.7, 0.3)
        assert d == pytest.approx(0.0, abs=1e-9)

    def test_known_euclidean_distance(self):
        """d = sqrt((0.5-(-0.5))^2 + (0.7-0.1)^2 + (0.3-0.8)^2)
           = sqrt(1.0 + 0.36 + 0.25) = sqrt(1.61) ~= 1.269"""
        d = _affect_distance(0.5, 0.7, 0.3, -0.5, 0.1, 0.8)
        expected = math.sqrt(1.0 + 0.36 + 0.25)
        assert d == pytest.approx(expected, abs=1e-6)

    def test_unit_step_valence(self):
        """Distance along valence axis by 1.0 should be exactly 1.0."""
        d = _affect_distance(0.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        assert d == pytest.approx(1.0, abs=1e-9)

    def test_unit_step_arousal(self):
        """Distance along arousal axis by 1.0 should be exactly 1.0."""
        d = _affect_distance(0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        assert d == pytest.approx(1.0, abs=1e-9)

    def test_unit_step_dominance(self):
        """Distance along dominance axis by 1.0 should be exactly 1.0."""
        d = _affect_distance(0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        assert d == pytest.approx(1.0, abs=1e-9)

    def test_symmetric(self):
        """Distance is symmetric: d(a, b) == d(b, a)."""
        d1 = _affect_distance(0.3, 0.5, 0.1, -0.2, 0.8, 0.6)
        d2 = _affect_distance(-0.2, 0.8, 0.6, 0.3, 0.5, 0.1)
        assert d1 == pytest.approx(d2, abs=1e-9)

    def test_returns_float(self):
        """Result is always a float."""
        d = _affect_distance(0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
        assert isinstance(d, float)

    def test_always_nonnegative(self):
        """Distance is always >= 0."""
        d = _affect_distance(-1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
        assert d >= 0.0
