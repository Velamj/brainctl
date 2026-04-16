"""Tests for schema-accelerated consolidation (Task C5).

Tse et al. 2007 [CLS-4]: memories with high entity-link density (>= 3
knowledge_edges to entities) skip normal episodic holding and are
immediately promoted to semantic during consolidation.
"""

import sqlite3

import pytest

from agentmemory.hippocampus import (
    SCHEMA_ACCELERATION_MIN_EDGES,
    accelerate_schema_consistent,
    find_schema_consistent_memories,
)


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL DEFAULT 'test',
            content TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'lesson',
            scope TEXT NOT NULL DEFAULT 'global',
            confidence REAL NOT NULL DEFAULT 0.5,
            alpha REAL DEFAULT 1.0, beta REAL DEFAULT 1.0,
            recalled_count INTEGER DEFAULT 0,
            memory_type TEXT DEFAULT 'episodic',
            temporal_class TEXT DEFAULT 'medium',
            ewc_importance REAL DEFAULT 0.0,
            protected INTEGER DEFAULT 0,
            salience_score REAL DEFAULT 0.5,
            tag_cycles_remaining INTEGER DEFAULT 0,
            stability REAL DEFAULT 1.0,
            labile_until TEXT DEFAULT NULL,
            retired_at TEXT DEFAULT NULL,
            last_recalled_at TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT NOT NULL, source_id INTEGER NOT NULL,
            target_table TEXT NOT NULL, target_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL, weight REAL DEFAULT 1.0,
            agent_id TEXT, co_activation_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, entity_type TEXT NOT NULL DEFAULT 'concept',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


def _insert_memory(db, content="test", memory_type="episodic",
                   temporal_class="medium", retired_at=None):
    db.execute(
        """INSERT INTO memories (content, memory_type, temporal_class)
           VALUES (?, ?, ?)""",
        (content, memory_type, temporal_class),
    )
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    if retired_at is not None:
        db.execute("UPDATE memories SET retired_at=? WHERE id=?", (retired_at, mid))
    db.commit()
    return mid


def _insert_entity(db, name="Entity"):
    db.execute("INSERT INTO entities (name, entity_type) VALUES (?, 'concept')", (name,))
    eid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    return eid


def _link_memory_to_entity(db, memory_id, entity_id, relation="mentions"):
    db.execute(
        """INSERT INTO knowledge_edges
           (source_table, source_id, target_table, target_id, relation_type)
           VALUES ('memories', ?, 'entities', ?, ?)""",
        (memory_id, entity_id, relation),
    )
    db.commit()


class TestFindSchemaConsistentMemories:
    def test_high_density_memory_identified(self):
        """A memory with >= 3 edges to entities must be returned."""
        db = _make_db()
        mid = _insert_memory(db, content="schema-rich fact")
        for i in range(SCHEMA_ACCELERATION_MIN_EDGES):
            eid = _insert_entity(db, name=f"Entity{i}")
            _link_memory_to_entity(db, mid, eid)

        result = find_schema_consistent_memories(db)
        assert mid in result

    def test_low_density_memory_excluded(self):
        """A memory with < 3 edges to entities must NOT be returned."""
        db = _make_db()
        mid = _insert_memory(db, content="sparse fact")
        # Add only (min_edges - 1) links
        for i in range(SCHEMA_ACCELERATION_MIN_EDGES - 1):
            eid = _insert_entity(db, name=f"SparseEntity{i}")
            _link_memory_to_entity(db, mid, eid)

        result = find_schema_consistent_memories(db)
        assert mid not in result

    def test_semantic_memory_excluded(self):
        """Memories already semantic must NOT be returned even with 3+ edges."""
        db = _make_db()
        mid = _insert_memory(db, content="already semantic", memory_type="semantic")
        for i in range(SCHEMA_ACCELERATION_MIN_EDGES):
            eid = _insert_entity(db, name=f"SemEntity{i}")
            _link_memory_to_entity(db, mid, eid)

        result = find_schema_consistent_memories(db)
        assert mid not in result

    def test_retired_memory_excluded(self):
        """Retired memories must NOT be returned even with 3+ edges."""
        db = _make_db()
        mid = _insert_memory(db, content="retired fact",
                              retired_at="2026-01-01T00:00:00")
        for i in range(SCHEMA_ACCELERATION_MIN_EDGES):
            eid = _insert_entity(db, name=f"RetEntity{i}")
            _link_memory_to_entity(db, mid, eid)

        result = find_schema_consistent_memories(db)
        assert mid not in result


class TestAccelerateSchemaConsistent:
    def test_promotion_changes_memory_type_and_class(self):
        """accelerate_schema_consistent must flip type→semantic and class→long."""
        db = _make_db()
        mid = _insert_memory(db, content="promotable", memory_type="episodic",
                              temporal_class="medium")
        for i in range(SCHEMA_ACCELERATION_MIN_EDGES):
            eid = _insert_entity(db, name=f"PromoEntity{i}")
            _link_memory_to_entity(db, mid, eid)

        stats = accelerate_schema_consistent(db)
        assert stats["promoted"] == 1

        row = db.execute(
            "SELECT memory_type, temporal_class FROM memories WHERE id=?", (mid,)
        ).fetchone()
        assert row["memory_type"] == "semantic"
        assert row["temporal_class"] == "long"

    def test_no_eligible_memories_returns_zero(self):
        """No high-density episodic memories → promoted=0."""
        db = _make_db()
        # Memory with only 1 edge — below threshold
        mid = _insert_memory(db, content="sparse")
        eid = _insert_entity(db, name="OnlyOne")
        _link_memory_to_entity(db, mid, eid)

        stats = accelerate_schema_consistent(db)
        assert stats["promoted"] == 0
