"""Tests for agentmemory.trust.apply_contradiction_penalty.

These tests exercise the shared helper directly with an explicitly-passed
``sqlite3.Connection`` — no MCP/CLI plumbing in the way. The
end-to-end MCP path is still covered by
``tests/test_mcp_tools_trust.py::TestTrustUpdateContradiction``; together
those two test files form the regression net for Bug 7 (2.2.0).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory.brain import Brain
from agentmemory.trust import apply_contradiction_penalty


# ---------------------------------------------------------------------------
# Fixtures — same pattern as tests/test_mcp_tools_trust.py so the helper sees
# the production schema (sqlite-vec / FTS5 setup is non-trivial; ``:memory:``
# would force us to hand-apply the schema).
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Fresh DB file with the production schema applied."""
    db_file = tmp_path / "brain.db"
    Brain(db_path=str(db_file))  # initialise schema
    return db_file


@pytest.fixture
def db(db_path):
    """Open a Row-factory connection on the temp DB and close it on teardown."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _insert_agent(db_path: Path, agent_id: str = "test-agent") -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR IGNORE INTO agents (id, display_name, agent_type, status, created_at, updated_at) "
        "VALUES (?, ?, 'test', 'active', strftime('%Y-%m-%dT%H:%M:%S','now'), strftime('%Y-%m-%dT%H:%M:%S','now'))",
        (agent_id, agent_id),
    )
    conn.commit()
    conn.close()


def _insert_memory(
    db_path: Path,
    content: str = "test memory",
    category: str = "project",
    agent_id: str = "test-agent",
    trust_score: float = 1.0,
) -> int:
    _insert_agent(db_path, agent_id)
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "INSERT INTO memories (agent_id, content, category, trust_score, alpha, beta, "
        "recalled_count, temporal_class, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 1.0, 1.0, 0, 'medium', "
        "strftime('%Y-%m-%dT%H:%M:%S','now'), strftime('%Y-%m-%dT%H:%M:%S','now'))",
        (agent_id, content, category, trust_score),
    )
    mem_id = cur.lastrowid
    conn.commit()
    conn.close()
    return mem_id


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------

class TestReturnShape:
    """Both pre-refactor surfaces exposed the same dict keys; helper must match."""

    def test_success_keys(self, db_path, db):
        mid_a = _insert_memory(db_path, trust_score=0.9, agent_id="agent-shape")
        mid_b = _insert_memory(db_path, trust_score=0.8, agent_id="agent-shape")
        result = apply_contradiction_penalty(db, mid_a, mid_b, resolved=False)
        assert set(result.keys()) == {
            "ok", "resolved", "loser_id", "winner_id", "tie", "updated_memories"
        }
        assert result["ok"] is True

    def test_validation_failure_keys(self, db_path, db):
        mid_a = _insert_memory(db_path, trust_score=0.8, agent_id="agent-shape")
        result = apply_contradiction_penalty(db, mid_a, 999_999, resolved=False)
        assert set(result.keys()) == {"ok", "error"}
        assert result["ok"] is False

    def test_updated_memories_is_list_of_dicts(self, db_path, db):
        mid_a = _insert_memory(db_path, trust_score=0.9, agent_id="agent-shape")
        mid_b = _insert_memory(db_path, trust_score=0.8, agent_id="agent-shape")
        result = apply_contradiction_penalty(db, mid_a, mid_b, resolved=False)
        assert isinstance(result["updated_memories"], list)
        assert len(result["updated_memories"]) == 2
        for row in result["updated_memories"]:
            assert isinstance(row, dict)
            assert "id" in row and "trust_score" in row


# ---------------------------------------------------------------------------
# Loser-by-trust semantics — the AGM rule Bug 7 fixed.
# ---------------------------------------------------------------------------

class TestLoserByTrust:
    def test_unresolved_lower_eats_full_penalty(self, db_path, db):
        # B is lower → loser, eats -0.20. A is higher → winner, untouched.
        mid_a = _insert_memory(db_path, trust_score=0.9, agent_id="agent-l")
        mid_b = _insert_memory(db_path, trust_score=0.8, agent_id="agent-l")
        result = apply_contradiction_penalty(db, mid_a, mid_b, resolved=False)
        assert result["loser_id"] == mid_b
        assert result["winner_id"] == mid_a
        assert result["tie"] is False
        scores = {m["id"]: m["trust_score"] for m in result["updated_memories"]}
        assert abs(scores[mid_a] - 0.9) < 1e-6
        assert abs(scores[mid_b] - 0.6) < 1e-6

    def test_arg_order_does_not_invert_outcome(self, db_path, db):
        # Same memories, swapped order — outcome must be identical (the bug).
        mid_high = _insert_memory(db_path, trust_score=0.9, agent_id="agent-o")
        mid_low = _insert_memory(db_path, trust_score=0.8, agent_id="agent-o")
        result = apply_contradiction_penalty(db, mid_low, mid_high, resolved=False)
        assert result["loser_id"] == mid_low
        assert result["winner_id"] == mid_high
        scores = {m["id"]: m["trust_score"] for m in result["updated_memories"]}
        assert abs(scores[mid_high] - 0.9) < 1e-6
        assert abs(scores[mid_low] - 0.6) < 1e-6

    def test_resolved_loser_minor_winner_reinforced(self, db_path, db):
        mid_high = _insert_memory(db_path, trust_score=0.9, agent_id="agent-r")
        mid_low = _insert_memory(db_path, trust_score=0.8, agent_id="agent-r")
        result = apply_contradiction_penalty(db, mid_high, mid_low, resolved=True)
        assert result["loser_id"] == mid_low
        assert result["winner_id"] == mid_high
        scores = {m["id"]: m["trust_score"] for m in result["updated_memories"]}
        assert abs(scores[mid_low] - 0.75) < 1e-6   # 0.8 - 0.05
        assert abs(scores[mid_high] - 0.92) < 1e-6  # 0.9 + 0.02


# ---------------------------------------------------------------------------
# Tie semantics
# ---------------------------------------------------------------------------

class TestTie:
    def test_tie_unresolved_both_eat_full_penalty(self, db_path, db):
        mid_a = _insert_memory(db_path, trust_score=0.7, agent_id="agent-t")
        mid_b = _insert_memory(db_path, trust_score=0.7, agent_id="agent-t")
        result = apply_contradiction_penalty(db, mid_a, mid_b, resolved=False)
        assert result["tie"] is True
        assert result["loser_id"] is None
        assert result["winner_id"] is None
        scores = {m["id"]: m["trust_score"] for m in result["updated_memories"]}
        assert abs(scores[mid_a] - 0.5) < 1e-6
        assert abs(scores[mid_b] - 0.5) < 1e-6

    def test_tie_resolved_both_eat_minor_penalty(self, db_path, db):
        mid_a = _insert_memory(db_path, trust_score=0.7, agent_id="agent-t")
        mid_b = _insert_memory(db_path, trust_score=0.7, agent_id="agent-t")
        result = apply_contradiction_penalty(db, mid_a, mid_b, resolved=True)
        assert result["tie"] is True
        scores = {m["id"]: m["trust_score"] for m in result["updated_memories"]}
        assert abs(scores[mid_a] - 0.65) < 1e-6
        assert abs(scores[mid_b] - 0.65) < 1e-6


# ---------------------------------------------------------------------------
# Clamps
# ---------------------------------------------------------------------------

class TestClamps:
    def test_floor_at_030_unresolved(self, db_path, db):
        mid_a = _insert_memory(db_path, trust_score=0.4, agent_id="agent-f")
        mid_b = _insert_memory(db_path, trust_score=0.35, agent_id="agent-f")
        result = apply_contradiction_penalty(db, mid_a, mid_b, resolved=False)
        for m in result["updated_memories"]:
            assert m["trust_score"] >= 0.30

    def test_floor_at_030_tie(self, db_path, db):
        mid_a = _insert_memory(db_path, trust_score=0.3, agent_id="agent-f")
        mid_b = _insert_memory(db_path, trust_score=0.3, agent_id="agent-f")
        result = apply_contradiction_penalty(db, mid_a, mid_b, resolved=False)
        for m in result["updated_memories"]:
            assert m["trust_score"] == 0.30

    def test_ceiling_at_one(self, db_path, db):
        mid_high = _insert_memory(db_path, trust_score=0.99, agent_id="agent-c")
        mid_low = _insert_memory(db_path, trust_score=0.5, agent_id="agent-c")
        result = apply_contradiction_penalty(db, mid_high, mid_low, resolved=True)
        scores = {m["id"]: m["trust_score"] for m in result["updated_memories"]}
        assert scores[mid_high] <= 1.0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_missing_one_id_returns_error(self, db_path, db):
        mid_a = _insert_memory(db_path, trust_score=0.8, agent_id="agent-v")
        result = apply_contradiction_penalty(db, mid_a, 999_999, resolved=False)
        assert result["ok"] is False
        assert "must exist" in result["error"]
        assert "1 of 2" in result["error"]

    def test_missing_both_ids_returns_error(self, db_path, db):
        result = apply_contradiction_penalty(db, 888_888, 999_999, resolved=False)
        assert result["ok"] is False
        assert "0 of 2" in result["error"]


# ---------------------------------------------------------------------------
# Connection-as-parameter contract — caller owns lifecycle.
# ---------------------------------------------------------------------------

class TestConnectionContract:
    """The helper must not close the connection it was given."""

    def test_does_not_close_connection(self, db_path, db):
        mid_a = _insert_memory(db_path, trust_score=0.9, agent_id="agent-conn")
        mid_b = _insert_memory(db_path, trust_score=0.8, agent_id="agent-conn")
        apply_contradiction_penalty(db, mid_a, mid_b, resolved=False)
        # Connection still usable after helper returns.
        cur = db.execute("SELECT COUNT(*) AS n FROM memories")
        assert cur.fetchone()["n"] >= 2

    def test_commits_on_success_so_followup_query_sees_update(self, db_path, db):
        mid_a = _insert_memory(db_path, trust_score=0.9, agent_id="agent-conn")
        mid_b = _insert_memory(db_path, trust_score=0.8, agent_id="agent-conn")
        apply_contradiction_penalty(db, mid_a, mid_b, resolved=False)
        # Re-open the connection — committed write must be visible.
        conn2 = sqlite3.connect(str(db_path))
        conn2.row_factory = sqlite3.Row
        try:
            row = conn2.execute(
                "SELECT trust_score FROM memories WHERE id = ?", (mid_b,)
            ).fetchone()
            assert abs(row["trust_score"] - 0.6) < 1e-6
        finally:
            conn2.close()
