"""Tests for Task 2: Typed Causal Edges + Counterfactual Attribution.

Covers:
    1. _add_causal_edge  — creates a typed edge
    2. _add_causal_edge  — rejects invalid causal_type
    3. _trace_causal_chain — follows causes/enables chain forward
    4. _trace_causal_chain — respects max_hops limit
    5. _counterfactual_attribution — returns correct memories_attributed count
    6. _counterfactual_attribution — verifies Q-values are boosted in the DB
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure src/ is importable
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import (
    _VALID_CAUSAL_TYPES,
    _add_causal_edge,
    _counterfactual_attribution,
    _trace_causal_chain,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    """In-memory SQLite DB with the minimal schema needed for causal tests."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL DEFAULT 'test',
            content TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'lesson',
            scope TEXT NOT NULL DEFAULT 'global',
            confidence REAL NOT NULL DEFAULT 0.5,
            q_value REAL DEFAULT 0.5,
            retired_at TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'test',
            summary TEXT NOT NULL DEFAULT '',
            event_type TEXT DEFAULT 'observation',
            importance REAL DEFAULT 0.5,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            target_table TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            agent_id TEXT,
            co_activation_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            CHECK (weight >= 0.0 AND weight <= 1.0)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ke
            ON knowledge_edges (source_table, source_id, target_table, target_id, relation_type);
    """)
    return db


def _insert_memory(db: sqlite3.Connection, content: str = "mem", q_value: float = 0.5) -> int:
    cur = db.execute(
        "INSERT INTO memories (content, q_value) VALUES (?, ?)",
        (content, q_value),
    )
    db.commit()
    return cur.lastrowid


def _insert_event(db: sqlite3.Connection, summary: str = "evt") -> int:
    cur = db.execute(
        "INSERT INTO events (summary) VALUES (?)",
        (summary,),
    )
    db.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Test 1: creates a typed causal edge
# ---------------------------------------------------------------------------

class TestAddCausalEdge:
    def test_creates_causes_edge(self):
        db = _make_db()
        m1 = _insert_memory(db, "cause memory")
        m2 = _insert_memory(db, "effect memory")

        result = _add_causal_edge(db, "memories", m1, "memories", m2, "causes")

        assert "error" not in result
        assert result["created"] is True
        assert result["edge_id"] is not None

        row = db.execute(
            "SELECT * FROM knowledge_edges WHERE source_id=? AND target_id=? "
            "AND relation_type='causes'",
            (m1, m2),
        ).fetchone()
        assert row is not None
        assert row["source_table"] == "memories"
        assert row["target_table"] == "memories"
        assert row["weight"] == 1.0

    def test_creates_enables_edge(self):
        db = _make_db()
        m1 = _insert_memory(db)
        m2 = _insert_memory(db)
        result = _add_causal_edge(db, "memories", m1, "memories", m2, "enables", weight=0.8)
        assert result["created"] is True
        row = db.execute(
            "SELECT relation_type, weight FROM knowledge_edges WHERE source_id=? AND target_id=?",
            (m1, m2),
        ).fetchone()
        assert row["relation_type"] == "enables"
        assert abs(row["weight"] - 0.8) < 1e-6

    def test_creates_prevents_edge(self):
        db = _make_db()
        m1 = _insert_memory(db)
        m2 = _insert_memory(db)
        result = _add_causal_edge(db, "memories", m1, "memories", m2, "prevents")
        assert result["created"] is True

    def test_idempotent_insert_or_ignore(self):
        db = _make_db()
        m1 = _insert_memory(db)
        m2 = _insert_memory(db)
        r1 = _add_causal_edge(db, "memories", m1, "memories", m2, "causes")
        r2 = _add_causal_edge(db, "memories", m1, "memories", m2, "causes")
        assert r1["created"] is True
        assert r2["created"] is False
        # Only one row
        count = db.execute("SELECT COUNT(*) FROM knowledge_edges").fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# Test 2: rejects invalid causal_type
# ---------------------------------------------------------------------------

class TestAddCausalEdgeInvalidType:
    def test_rejects_unknown_type(self):
        db = _make_db()
        m1 = _insert_memory(db)
        m2 = _insert_memory(db)
        result = _add_causal_edge(db, "memories", m1, "memories", m2, "triggers")
        assert "error" in result
        assert "triggers" in result["error"]

    def test_rejects_empty_string(self):
        db = _make_db()
        m1 = _insert_memory(db)
        m2 = _insert_memory(db)
        result = _add_causal_edge(db, "memories", m1, "memories", m2, "")
        assert "error" in result

    def test_rejects_mentions_type(self):
        """'mentions' is a valid edge type globally but not a causal type."""
        db = _make_db()
        m1 = _insert_memory(db)
        m2 = _insert_memory(db)
        result = _add_causal_edge(db, "memories", m1, "memories", m2, "mentions")
        assert "error" in result

    def test_valid_causal_types_constant(self):
        assert _VALID_CAUSAL_TYPES == {"causes", "enables", "prevents"}


# ---------------------------------------------------------------------------
# Test 3: _trace_causal_chain follows chain forward
# ---------------------------------------------------------------------------

class TestTraceCausalChain:
    def test_follows_chain(self):
        db = _make_db()
        m1 = _insert_memory(db, "root")
        m2 = _insert_memory(db, "mid")
        m3 = _insert_memory(db, "leaf")
        _add_causal_edge(db, "memories", m1, "memories", m2, "causes")
        _add_causal_edge(db, "memories", m2, "memories", m3, "enables")

        chain = _trace_causal_chain(db, "memories", m1)

        assert len(chain) == 2
        ids = [c["target_id"] for c in chain]
        assert m2 in ids
        assert m3 in ids

    def test_chain_contains_required_fields(self):
        db = _make_db()
        m1 = _insert_memory(db)
        m2 = _insert_memory(db)
        _add_causal_edge(db, "memories", m1, "memories", m2, "causes")

        chain = _trace_causal_chain(db, "memories", m1)
        assert len(chain) == 1
        node = chain[0]
        assert "target_table" in node
        assert "target_id" in node
        assert "relation_type" in node
        assert "weight" in node
        assert node["hop"] == 1

    def test_excludes_prevents_edges(self):
        """'prevents' edges must NOT be followed during forward traversal."""
        db = _make_db()
        m1 = _insert_memory(db)
        m2 = _insert_memory(db)
        _add_causal_edge(db, "memories", m1, "memories", m2, "prevents")

        chain = _trace_causal_chain(db, "memories", m1)
        assert chain == []

    def test_stops_at_cycles(self):
        """A cycle A -> B -> A must not loop forever."""
        db = _make_db()
        m1 = _insert_memory(db)
        m2 = _insert_memory(db)
        # Insert edges manually to bypass unique index direction
        db.execute(
            "INSERT INTO knowledge_edges (source_table, source_id, target_table, target_id, "
            "relation_type, weight) VALUES ('memories',?,'memories',?,'causes',1.0)",
            (m1, m2),
        )
        db.execute(
            "INSERT INTO knowledge_edges (source_table, source_id, target_table, target_id, "
            "relation_type, weight) VALUES ('memories',?,'memories',?,'causes',1.0)",
            (m2, m1),
        )
        db.commit()
        chain = _trace_causal_chain(db, "memories", m1)
        # Only m2 should appear; m1 is visited so cycle is cut
        assert len(chain) == 1
        assert chain[0]["target_id"] == m2

    def test_empty_chain_for_isolated_node(self):
        db = _make_db()
        m1 = _insert_memory(db)
        assert _trace_causal_chain(db, "memories", m1) == []


# ---------------------------------------------------------------------------
# Test 4: _trace_causal_chain respects max_hops
# ---------------------------------------------------------------------------

class TestTraceCausalChainMaxHops:
    def test_respects_max_hops_1(self):
        db = _make_db()
        m1 = _insert_memory(db, "A")
        m2 = _insert_memory(db, "B")
        m3 = _insert_memory(db, "C")
        _add_causal_edge(db, "memories", m1, "memories", m2, "causes")
        _add_causal_edge(db, "memories", m2, "memories", m3, "causes")

        chain = _trace_causal_chain(db, "memories", m1, max_hops=1)
        ids = [c["target_id"] for c in chain]
        assert m2 in ids
        assert m3 not in ids

    def test_respects_max_hops_0(self):
        db = _make_db()
        m1 = _insert_memory(db)
        m2 = _insert_memory(db)
        _add_causal_edge(db, "memories", m1, "memories", m2, "causes")

        chain = _trace_causal_chain(db, "memories", m1, max_hops=0)
        assert chain == []

    def test_respects_max_hops_deep(self):
        """Build a 6-hop chain, request max_hops=3 — only 3 nodes returned."""
        db = _make_db()
        nodes = [_insert_memory(db, f"node{i}") for i in range(7)]
        for i in range(6):
            _add_causal_edge(db, "memories", nodes[i], "memories", nodes[i + 1], "causes")

        chain = _trace_causal_chain(db, "memories", nodes[0], max_hops=3)
        assert len(chain) == 3
        assert all(c["hop"] <= 3 for c in chain)


# ---------------------------------------------------------------------------
# Test 5: _counterfactual_attribution — correct memories_attributed count
# ---------------------------------------------------------------------------

class TestCounterfactualAttribution:
    def test_attributes_contributing_memories(self):
        db = _make_db()
        m1 = _insert_memory(db, "memory A", q_value=0.5)
        m2 = _insert_memory(db, "memory B", q_value=0.5)
        evt = _insert_event(db, "positive outcome")

        # m1 -causes-> m2 -causes-> event
        _add_causal_edge(db, "memories", m1, "memories", m2, "causes")
        _add_causal_edge(db, "memories", m2, "events", evt, "causes")

        result = _counterfactual_attribution(db, evt)
        assert result["memories_attributed"] == 2

    def test_zero_attributed_for_unlinked_event(self):
        db = _make_db()
        evt = _insert_event(db, "orphan event")
        result = _counterfactual_attribution(db, evt)
        assert result["memories_attributed"] == 0

    def test_only_memory_nodes_counted(self):
        """Non-memory source nodes (e.g. entities) do not count toward attributed."""
        db = _make_db()
        m1 = _insert_memory(db)
        evt = _insert_event(db)
        # memory causes event
        _add_causal_edge(db, "memories", m1, "events", evt, "causes")
        result = _counterfactual_attribution(db, evt)
        assert result["memories_attributed"] == 1

    def test_prevents_edges_not_traversed_backward(self):
        """'prevents' edges must not be followed backward."""
        db = _make_db()
        m1 = _insert_memory(db)
        evt = _insert_event(db)
        _add_causal_edge(db, "memories", m1, "events", evt, "prevents")
        result = _counterfactual_attribution(db, evt)
        assert result["memories_attributed"] == 0


# ---------------------------------------------------------------------------
# Test 6: _counterfactual_attribution boosts Q-values
# ---------------------------------------------------------------------------

class TestCounterfactualQValueBoost:
    def test_positive_outcome_boosts_q_value(self):
        db = _make_db()
        m1 = _insert_memory(db, "contributor", q_value=0.5)
        evt = _insert_event(db)
        _add_causal_edge(db, "memories", m1, "events", evt, "causes", weight=1.0)

        _counterfactual_attribution(db, evt, outcome_positive=True)

        row = db.execute("SELECT q_value FROM memories WHERE id=?", (m1,)).fetchone()
        # q_new = 0.5 + 0.1*1.0*(1.0-0.5) = 0.5 + 0.05 = 0.55
        assert abs(row["q_value"] - 0.55) < 1e-6

    def test_negative_outcome_lowers_q_value(self):
        db = _make_db()
        m1 = _insert_memory(db, "bad contributor", q_value=0.5)
        evt = _insert_event(db)
        _add_causal_edge(db, "memories", m1, "events", evt, "causes", weight=1.0)

        _counterfactual_attribution(db, evt, outcome_positive=False)

        row = db.execute("SELECT q_value FROM memories WHERE id=?", (m1,)).fetchone()
        # q_new = 0.5 + 0.1*1.0*(0.0-0.5) = 0.5 - 0.05 = 0.45
        assert abs(row["q_value"] - 0.45) < 1e-6

    def test_edge_weight_scales_learning_rate(self):
        """Half-weight edge should produce half the Q-value change."""
        db = _make_db()
        m1 = _insert_memory(db, "half-weight", q_value=0.5)
        evt = _insert_event(db)
        _add_causal_edge(db, "memories", m1, "events", evt, "enables", weight=0.5)

        _counterfactual_attribution(db, evt, outcome_positive=True)

        row = db.execute("SELECT q_value FROM memories WHERE id=?", (m1,)).fetchone()
        # q_new = 0.5 + 0.1*0.5*(1.0-0.5) = 0.5 + 0.025 = 0.525
        assert abs(row["q_value"] - 0.525) < 1e-6

    def test_q_value_clamped_to_one(self):
        """Q-value must not exceed 1.0 even after many boosts."""
        db = _make_db()
        m1 = _insert_memory(db, "near-max", q_value=0.99)
        evt = _insert_event(db)
        _add_causal_edge(db, "memories", m1, "events", evt, "causes", weight=1.0)

        _counterfactual_attribution(db, evt, outcome_positive=True)

        row = db.execute("SELECT q_value FROM memories WHERE id=?", (m1,)).fetchone()
        assert row["q_value"] <= 1.0

    def test_q_value_clamped_to_zero(self):
        """Q-value must not go below 0.0 after penalty."""
        db = _make_db()
        m1 = _insert_memory(db, "near-min", q_value=0.01)
        evt = _insert_event(db)
        _add_causal_edge(db, "memories", m1, "events", evt, "causes", weight=1.0)

        _counterfactual_attribution(db, evt, outcome_positive=False)

        row = db.execute("SELECT q_value FROM memories WHERE id=?", (m1,)).fetchone()
        assert row["q_value"] >= 0.0

    def test_retired_memories_not_updated(self):
        """Retired memories must be skipped — the SELECT filters on retired_at IS NULL."""
        db = _make_db()
        m1 = _insert_memory(db, "retired", q_value=0.5)
        db.execute(
            "UPDATE memories SET retired_at=strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id=?",
            (m1,),
        )
        db.commit()
        evt = _insert_event(db)
        _add_causal_edge(db, "memories", m1, "events", evt, "causes")

        result = _counterfactual_attribution(db, evt, outcome_positive=True)

        # Attributed count is 1 (the edge is found) but Q-value is unchanged
        # because the memory is retired
        row = db.execute("SELECT q_value FROM memories WHERE id=?", (m1,)).fetchone()
        assert row["q_value"] == 0.5
