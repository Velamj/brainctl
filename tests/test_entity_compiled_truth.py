"""Tests for the entity compiled_truth / enrichment_tier / aliases additions.

Covers migrations 033–035 and the matching cmd_entity_compile,
cmd_entity_tier, cmd_entity_alias handlers in _impl.py.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory.brain import Brain  # noqa: E402
from agentmemory import migrate as _migrate  # noqa: E402
import agentmemory._impl as _impl  # noqa: E402


def _fresh_db(tmp_path) -> Path:
    """Build a Brain with sample data and return the path.

    init_schema.sql already carries every migration's effects up through 036
    (compiled_truth/tier/aliases/self-healing gap_type CHECK), so no migrate
    step is needed for tests — the schema is already in its final state.
    We still backfill schema_versions to the highest applied migration so
    the Brain migration-warning path stays quiet.
    """
    db = tmp_path / "brain.db"
    brain = Brain(db_path=str(db), agent_id="test")
    brain.remember("Alice is the primary maintainer of retrieval", category="user")
    brain.remember("Alice uses dark mode", category="preference")
    brain.entity("Alice", "person", observations=["Security reviewer", "Python expert"])
    brain.close()

    # Mark every migration on disk as applied so `brainctl migrate` is a no-op
    # when Brain re-opens the file later.
    _migrate.mark_applied_up_to(str(db), 999)
    return db


@pytest.fixture(autouse=True)
def _restore_json_out():
    """Snapshot + restore `_impl.json_out` around every test so the capture
    patch below doesn't leak into unrelated tests in the same pytest run
    (they parse json_out stdout and crash on our lambda's empty return)."""
    _saved = _impl.json_out
    yield
    _impl.json_out = _saved


def _dispatch(args) -> list:
    captured: list[dict] = []
    _impl.json_out = lambda d, compact=False: captured.append(d)
    return captured


class TestCompiledTruth:
    def test_compile_single_entity(self, tmp_path):
        db = _fresh_db(tmp_path)
        _impl.DB_PATH = db
        captured = _dispatch(None)
        _impl.cmd_entity_compile(types.SimpleNamespace(
            identifier="Alice", all=False, agent="test",
        ))
        result = captured[-1]
        assert result["ok"]
        assert "Alice" in result["compiled_truth"]
        assert "Security reviewer" in result["compiled_truth"]
        assert result["source_count"] >= 1

    def test_compile_all(self, tmp_path):
        db = _fresh_db(tmp_path)
        _impl.DB_PATH = db
        captured = _dispatch(None)
        _impl.cmd_entity_compile(types.SimpleNamespace(
            identifier=None, all=True, agent="test",
        ))
        result = captured[-1]
        assert result["ok"]
        assert result["updated"] >= 1

    def test_get_compiled_flag(self, tmp_path):
        db = _fresh_db(tmp_path)
        _impl.DB_PATH = db
        # First populate compiled_truth
        captured = _dispatch(None)
        _impl.cmd_entity_compile(types.SimpleNamespace(
            identifier="Alice", all=False, agent="test",
        ))
        captured.clear()
        _impl.cmd_entity_get(types.SimpleNamespace(
            identifier="Alice", compiled=True, agent="test",
        ))
        result = captured[-1]
        assert result["ok"]
        assert "compiled_truth" in result
        assert result["compiled_truth_updated_at"] is not None


class TestEnrichmentTier:
    def test_tier_defaults_to_3(self, tmp_path):
        db = _fresh_db(tmp_path)
        _impl.DB_PATH = db
        captured = _dispatch(None)
        _impl.cmd_entity_tier(types.SimpleNamespace(
            identifier="Alice", refresh=False, agent="test",
        ))
        result = captured[-1]
        assert result["ok"]
        assert result["current_tier"] == 3
        # Computed tier also 3 for this lightly-linked fixture
        assert result["computed_tier"] == 3
        assert result["signals"]["score"] >= 0

    def test_tier_refresh_updates_all(self, tmp_path):
        db = _fresh_db(tmp_path)
        _impl.DB_PATH = db
        captured = _dispatch(None)
        _impl.cmd_entity_tier(types.SimpleNamespace(
            identifier=None, refresh=True, agent="test",
        ))
        result = captured[-1]
        assert result["ok"]
        assert sum(result["distribution"].values()) == result["refreshed"]

    def test_tier_promotion_signal(self, tmp_path):
        """Heavy recall + many edges should push a fixture into T1."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        ent_id = conn.execute("SELECT id FROM entities WHERE name='Alice'").fetchone()["id"]
        # Fake a busy entity: many edges + high recall counts.
        for mid in conn.execute("SELECT id FROM memories").fetchall():
            conn.execute(
                "INSERT INTO knowledge_edges (source_table, source_id, target_table, target_id, relation_type, agent_id) "
                "VALUES ('entities', ?, 'memories', ?, 'mentions', 'test')",
                (ent_id, mid["id"]),
            )
            conn.execute(
                "UPDATE memories SET recalled_count = 20 WHERE id = ?",
                (mid["id"],),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM entities WHERE id=?", (ent_id,)).fetchone()
        tier, signals = _impl.compute_entity_tier(conn, row)
        conn.close()
        assert signals["mem_recalls"] >= 40
        assert tier in (1, 2)  # heavy signal pushes above T3 floor


class TestAliases:
    def test_add_and_list(self, tmp_path):
        db = _fresh_db(tmp_path)
        _impl.DB_PATH = db
        captured = _dispatch(None)
        _impl.cmd_entity_alias(types.SimpleNamespace(
            alias_action="add", identifier="Alice",
            values=["A. Chen", "alicec"], agent="test",
        ))
        assert captured[-1]["aliases"] == ["A. Chen", "alicec"]

        captured.clear()
        _impl.cmd_entity_alias(types.SimpleNamespace(
            alias_action="list", identifier="Alice",
            values=None, agent="test",
        ))
        assert captured[-1]["aliases"] == ["A. Chen", "alicec"]

    def test_remove(self, tmp_path):
        db = _fresh_db(tmp_path)
        _impl.DB_PATH = db
        captured = _dispatch(None)
        _impl.cmd_entity_alias(types.SimpleNamespace(
            alias_action="add", identifier="Alice",
            values=["foo", "bar"], agent="test",
        ))
        captured.clear()
        _impl.cmd_entity_alias(types.SimpleNamespace(
            alias_action="remove", identifier="Alice",
            values=["foo"], agent="test",
        ))
        assert "foo" not in captured[-1]["aliases"]
        assert "bar" in captured[-1]["aliases"]

    def test_find_by_alias(self, tmp_path):
        db = _fresh_db(tmp_path)
        _impl.DB_PATH = db
        captured = _dispatch(None)
        _impl.cmd_entity_alias(types.SimpleNamespace(
            alias_action="add", identifier="Alice",
            values=["alicec"], agent="test",
        ))
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = _impl.find_entity_by_alias(conn, "alicec")
        conn.close()
        assert row is not None
        assert row["name"] == "Alice"


class TestGapsSelfHealing:
    def test_scan_reports_orphan_memory(self, tmp_path):
        db = _fresh_db(tmp_path)
        # Age memories to past the threshold
        conn = sqlite3.connect(str(db))
        conn.execute(
            "UPDATE memories SET created_at = strftime('%Y-%m-%dT%H:%M:%S', datetime('now','-60 days'))"
        )
        conn.commit()
        conn.close()
        _impl.DB_PATH = db
        captured = _dispatch(None)
        _impl.cmd_gaps_scan(types.SimpleNamespace(skip_self_healing=False))
        report = captured[-1]
        assert "orphan_memories" in report
        assert len(report["orphan_memories"]) >= 1

    def test_scan_reports_broken_edge(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO knowledge_edges (source_table, source_id, target_table, target_id, relation_type, agent_id) "
            "VALUES ('entities', 99999, 'memories', 99999, 'mentions', 'test')"
        )
        conn.commit()
        conn.close()
        _impl.DB_PATH = db
        captured = _dispatch(None)
        _impl.cmd_gaps_scan(types.SimpleNamespace(skip_self_healing=False))
        report = captured[-1]
        assert len(report["broken_edges"]) >= 1

    def test_skip_self_healing_flag(self, tmp_path):
        db = _fresh_db(tmp_path)
        _impl.DB_PATH = db
        captured = _dispatch(None)
        _impl.cmd_gaps_scan(types.SimpleNamespace(skip_self_healing=True))
        report = captured[-1]
        assert report["orphan_memories"] == []
        assert report["broken_edges"] == []
        assert report["unreferenced_entities"] == []
