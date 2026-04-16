"""Tests for homeostatic pressure computation (Task B2)."""

import sqlite3

import pytest

from agentmemory.hippocampus import (
    HOMEOSTATIC_SETPOINT,
    LEARNING_LOAD_THRESHOLD,
    compute_homeostatic_pressure,
    compute_learning_load,
    should_trigger_consolidation,
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
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'test', summary TEXT NOT NULL,
            event_type TEXT DEFAULT 'observation', importance REAL DEFAULT 0.5,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


def _insert_memory(db, content="test", confidence=0.5, category="lesson",
                   temporal_class="medium", ewc_importance=0.0,
                   tag_cycles_remaining=0, stability=1.0,
                   recalled_count=0, memory_type="episodic",
                   protected=0, salience_score=0.5, agent_id="test",
                   created_at=None, retired_at=None):
    if created_at:
        db.execute(
            """INSERT INTO memories (agent_id, content, category, scope, confidence,
               temporal_class, ewc_importance, tag_cycles_remaining, stability,
               recalled_count, memory_type, protected, salience_score,
               created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (agent_id, content, category, "global", confidence, temporal_class,
             ewc_importance, tag_cycles_remaining, stability, recalled_count,
             memory_type, protected, salience_score, created_at, created_at))
    else:
        db.execute(
            """INSERT INTO memories (agent_id, content, category, scope, confidence,
               temporal_class, ewc_importance, tag_cycles_remaining, stability,
               recalled_count, memory_type, protected, salience_score)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (agent_id, content, category, "global", confidence, temporal_class,
             ewc_importance, tag_cycles_remaining, stability, recalled_count,
             memory_type, protected, salience_score))
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    if retired_at is not None:
        db.execute("UPDATE memories SET retired_at=? WHERE id=?", (retired_at, mid))
    db.commit()
    return mid


class TestHomeostaticPressure:
    def test_pressure_is_mean_confidence(self):
        """3 memories with conf 0.8, 0.6, 0.4 → pressure ≈ 0.6"""
        db = _make_db()
        _insert_memory(db, content="a", confidence=0.8)
        _insert_memory(db, content="b", confidence=0.6)
        _insert_memory(db, content="c", confidence=0.4)
        pressure = compute_homeostatic_pressure(db)
        assert abs(pressure - 0.6) < 1e-9

    def test_pressure_excludes_retired(self):
        """Retired memories don't count toward pressure."""
        db = _make_db()
        _insert_memory(db, content="active", confidence=0.8)
        _insert_memory(db, content="retired", confidence=1.0,
                       retired_at="2026-01-01T00:00:00")
        # Only the active memory (0.8) should count
        pressure = compute_homeostatic_pressure(db)
        assert abs(pressure - 0.8) < 1e-9

    def test_empty_db_returns_zero(self):
        """No memories → 0.0 (not a division-by-zero error)."""
        db = _make_db()
        pressure = compute_homeostatic_pressure(db)
        assert pressure == 0.0

    def test_learning_load_counts_recent(self):
        """Only memories created after 'since' timestamp are counted."""
        db = _make_db()
        _insert_memory(db, content="old",  created_at="2026-01-01T00:00:00")
        _insert_memory(db, content="new1", created_at="2026-02-01T00:00:00")
        _insert_memory(db, content="new2", created_at="2026-03-01T00:00:00")
        # since = 2026-01-15, so only new1 and new2 qualify
        load = compute_learning_load(db, since="2026-01-15T00:00:00")
        assert load == 2

    def test_learning_load_no_since_returns_zero(self):
        """Calling with no since= argument returns 0."""
        db = _make_db()
        _insert_memory(db, content="x")
        assert compute_learning_load(db) == 0

    def test_should_trigger_above_setpoint(self):
        """pressure > setpoint → True regardless of load."""
        assert should_trigger_consolidation(HOMEOSTATIC_SETPOINT + 0.01) is True

    def test_should_trigger_at_setpoint_is_false(self):
        """pressure == setpoint → False (strictly greater than required)."""
        assert should_trigger_consolidation(HOMEOSTATIC_SETPOINT) is False

    def test_should_trigger_high_load(self):
        """learning_load > threshold → True even if pressure is low."""
        assert should_trigger_consolidation(
            pressure=0.1,
            learning_load=LEARNING_LOAD_THRESHOLD + 1
        ) is True

    def test_should_trigger_low_pressure_low_load(self):
        """Neither condition met → False."""
        assert should_trigger_consolidation(
            pressure=0.1,
            learning_load=5
        ) is False
