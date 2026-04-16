"""Tests for access-pattern-driven replay selection (Task C7).

Yang & Buzsaki 2024, Ramirez-Villegas et al. 2025
"""

import sqlite3

import pytest

from agentmemory.hippocampus import select_importance_weighted_replay


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
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'test',
            summary TEXT NOT NULL,
            event_type TEXT DEFAULT 'observation',
            importance REAL DEFAULT 0.5,
            project TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER,
            agent_id TEXT,
            access_type TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


def _insert_memory(db, content="test", salience_score=0.5, confidence=0.5,
                   category="lesson", memory_type="episodic",
                   retired_at=None, created_at=None):
    if created_at:
        db.execute(
            """INSERT INTO memories (agent_id, content, category, scope, confidence,
               salience_score, memory_type, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            ("test", content, category, "global", confidence,
             salience_score, memory_type, created_at, created_at))
    else:
        db.execute(
            """INSERT INTO memories (agent_id, content, category, scope, confidence,
               salience_score, memory_type) VALUES (?,?,?,?,?,?,?)""",
            ("test", content, category, "global", confidence, salience_score, memory_type))
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    if retired_at is not None:
        db.execute("UPDATE memories SET retired_at=? WHERE id=?", (retired_at, mid))
    db.commit()
    return mid


def _insert_event(db, summary="event", importance=0.5, created_at=None):
    if created_at:
        db.execute(
            "INSERT INTO events (agent_id, summary, importance, created_at) VALUES (?,?,?,?)",
            ("test", summary, importance, created_at))
    else:
        db.execute(
            "INSERT INTO events (agent_id, summary, importance) VALUES (?,?,?)",
            ("test", summary, importance))
    eid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    return eid


# ---------------------------------------------------------------------------
# Test 1: Memories near high-importance events are prioritized
# ---------------------------------------------------------------------------

class TestImportanceWeightedPrioritization:
    def test_co_temporal_memory_ranks_higher(self):
        """Memory created within 2h of a high-importance event (>=0.7) should
        have higher replay_weight than an isolated memory with same salience."""
        db = _make_db()
        # Memory A: co-temporal with an important event
        mid_a = _insert_memory(db, content="A near event",
                                salience_score=0.5,
                                created_at="2026-04-01 10:00:00")
        _insert_event(db, summary="big event", importance=0.9,
                      created_at="2026-04-01 10:30:00")

        # Memory B: no co-temporal important event, same salience
        mid_b = _insert_memory(db, content="B isolated",
                                salience_score=0.5,
                                created_at="2026-04-01 20:00:00")

        results = select_importance_weighted_replay(db, top_k=10)
        assert len(results) >= 2

        by_id = {r["id"]: r for r in results}
        assert mid_a in by_id
        assert mid_b in by_id
        # A co-temporal with importance=0.9 event → higher replay_weight
        assert by_id[mid_a]["replay_weight"] > by_id[mid_b]["replay_weight"]

    def test_high_importance_event_boosts_replay_weight(self):
        """Event with importance=1.0 should drive max replay_weight boost."""
        db = _make_db()
        mid = _insert_memory(db, content="critical memory",
                              salience_score=0.8,
                              created_at="2026-04-01 10:00:00")
        _insert_event(db, summary="critical event", importance=1.0,
                      created_at="2026-04-01 10:15:00")

        results = select_importance_weighted_replay(db, top_k=5)
        assert len(results) == 1
        r = results[0]
        # replay_weight = salience_score * max_event_importance = 0.8 * 1.0
        assert abs(r["replay_weight"] - 0.8) < 1e-6
        assert r["max_event_importance"] == 1.0

    def test_event_outside_2h_window_not_counted(self):
        """An event >2h away from memory creation should not elevate max_event_importance."""
        db = _make_db()
        mid = _insert_memory(db, content="isolated memory",
                              salience_score=0.6,
                              created_at="2026-04-01 10:00:00")
        # event is 3 hours after the memory — outside the +2h window
        _insert_event(db, summary="distant event", importance=0.9,
                      created_at="2026-04-01 13:01:00")

        results = select_importance_weighted_replay(db, top_k=5)
        assert len(results) == 1
        r = results[0]
        # No qualifying event → max_event_importance falls back to COALESCE default 0.5
        assert abs(r["max_event_importance"] - 0.5) < 1e-6
        # replay_weight = salience_score * 0.5
        assert abs(r["replay_weight"] - (0.6 * 0.5)) < 1e-6


# ---------------------------------------------------------------------------
# Test 2: Respects top_k limit
# ---------------------------------------------------------------------------

class TestTopKLimit:
    def test_top_k_limits_results(self):
        """Result count must not exceed top_k even with many memories."""
        db = _make_db()
        for i in range(30):
            _insert_memory(db, content=f"mem {i}", salience_score=0.5)

        results = select_importance_weighted_replay(db, top_k=10)
        assert len(results) == 10

    def test_top_k_default_is_20(self):
        """Default top_k=20 is respected."""
        db = _make_db()
        for i in range(25):
            _insert_memory(db, content=f"mem {i}", salience_score=0.5)

        results = select_importance_weighted_replay(db)
        assert len(results) == 20


# ---------------------------------------------------------------------------
# Test 3: Empty DB returns empty list
# ---------------------------------------------------------------------------

class TestEmptyDB:
    def test_empty_db_returns_empty_list(self):
        """No memories → empty list, no exception."""
        db = _make_db()
        results = select_importance_weighted_replay(db, top_k=20)
        assert results == []

    def test_events_only_no_memories(self):
        """Events present but no memories → empty list."""
        db = _make_db()
        _insert_event(db, summary="some event", importance=0.9)
        results = select_importance_weighted_replay(db, top_k=20)
        assert results == []


# ---------------------------------------------------------------------------
# Test 4: Scales by importance — higher event importance → higher replay_weight
# ---------------------------------------------------------------------------

class TestScalesByImportance:
    def test_higher_event_importance_higher_weight(self):
        """Two equal-salience memories: the one near a higher-importance event
        should have a proportionally higher replay_weight."""
        db = _make_db()
        # Memory X: near event with importance=0.9
        mid_x = _insert_memory(db, content="near high", salience_score=0.6,
                                created_at="2026-04-01 08:00:00")
        _insert_event(db, summary="high event", importance=0.9,
                      created_at="2026-04-01 08:30:00")

        # Memory Y: near event with importance=0.75
        mid_y = _insert_memory(db, content="near medium", salience_score=0.6,
                                created_at="2026-04-02 08:00:00")
        _insert_event(db, summary="medium event", importance=0.75,
                      created_at="2026-04-02 08:30:00")

        results = select_importance_weighted_replay(db, top_k=10)
        by_id = {r["id"]: r for r in results}

        rw_x = by_id[mid_x]["replay_weight"]
        rw_y = by_id[mid_y]["replay_weight"]
        # 0.6 * 0.9 = 0.54  vs  0.6 * 0.75 = 0.45
        assert rw_x > rw_y

    def test_replay_weight_formula_is_salience_times_event_importance(self):
        """replay_weight must equal salience_score * max_event_importance."""
        db = _make_db()
        mid = _insert_memory(db, content="test mem", salience_score=0.7,
                              created_at="2026-04-01 10:00:00")
        _insert_event(db, summary="evt", importance=0.8,
                      created_at="2026-04-01 10:45:00")

        results = select_importance_weighted_replay(db, top_k=5)
        assert len(results) == 1
        r = results[0]
        expected = (r.get("salience_score") or 0.5) * (r.get("max_event_importance") or 0.5)
        assert abs(r["replay_weight"] - expected) < 1e-9

    def test_non_episodic_memories_excluded(self):
        """Only episodic memories should be returned."""
        db = _make_db()
        _insert_memory(db, content="semantic mem", memory_type="semantic",
                       salience_score=0.9)
        mid_ep = _insert_memory(db, content="episodic mem", memory_type="episodic",
                                 salience_score=0.5)

        results = select_importance_weighted_replay(db, top_k=10)
        ids = [r["id"] for r in results]
        assert mid_ep in ids
        # semantic memory must not appear
        for r in results:
            assert r["id"] != mid_ep or True  # episodic is fine
        # all returned must be episodic (no direct column, but semantic should not appear)
        assert all(r["id"] == mid_ep for r in results)

    def test_low_importance_event_not_counted(self):
        """Events with importance < 0.7 should not affect max_event_importance
        (the JOIN condition filters importance >= 0.7)."""
        db = _make_db()
        mid = _insert_memory(db, content="test", salience_score=0.5,
                              created_at="2026-04-01 10:00:00")
        # This event has importance < 0.7, so it is excluded by the JOIN
        _insert_event(db, summary="low event", importance=0.5,
                      created_at="2026-04-01 10:30:00")

        results = select_importance_weighted_replay(db, top_k=5)
        assert len(results) == 1
        r = results[0]
        # max_event_importance should fall back to COALESCE default 0.5
        assert abs(r["max_event_importance"] - 0.5) < 1e-6
