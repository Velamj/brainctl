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


class TestProportionalDownscaling:
    def test_downscaling_reduces_confidence(self):
        """All non-protected memories should lose confidence."""
        db = _make_db()
        _insert_memory(db, content="a", confidence=0.8)
        _insert_memory(db, content="b", confidence=0.6)
        from agentmemory.hippocampus import apply_proportional_downscaling
        stats = apply_proportional_downscaling(db, downscale_factor=0.9)
        rows = db.execute("SELECT confidence FROM memories ORDER BY id").fetchall()
        assert rows[0]["confidence"] < 0.8
        assert rows[1]["confidence"] < 0.6

    def test_tagged_memories_exempt(self):
        """Memories with tag_cycles_remaining > 0 skip downscaling."""
        db = _make_db()
        m1 = _insert_memory(db, content="tagged", confidence=0.8, tag_cycles_remaining=2)
        m2 = _insert_memory(db, content="untagged", confidence=0.8, tag_cycles_remaining=0)
        from agentmemory.hippocampus import apply_proportional_downscaling
        apply_proportional_downscaling(db, downscale_factor=0.8)
        r1 = db.execute("SELECT confidence FROM memories WHERE id=?", (m1,)).fetchone()
        r2 = db.execute("SELECT confidence FROM memories WHERE id=?", (m2,)).fetchone()
        assert abs(r1["confidence"] - 0.8) < 0.001  # unchanged
        assert r2["confidence"] < 0.8  # downscaled

    def test_importance_resists_downscaling(self):
        """High-importance memories should be downscaled less."""
        db = _make_db()
        m1 = _insert_memory(db, content="important", confidence=0.8, ewc_importance=0.9)
        m2 = _insert_memory(db, content="unimportant", confidence=0.8, ewc_importance=0.1)
        from agentmemory.hippocampus import apply_proportional_downscaling
        apply_proportional_downscaling(db, downscale_factor=0.8)
        r1 = db.execute("SELECT confidence FROM memories WHERE id=?", (m1,)).fetchone()
        r2 = db.execute("SELECT confidence FROM memories WHERE id=?", (m2,)).fetchone()
        assert r1["confidence"] > r2["confidence"]

    def test_retirement_below_threshold(self):
        """Memories below retirement threshold get retired."""
        db = _make_db()
        _insert_memory(db, content="dying", confidence=0.05)
        from agentmemory.hippocampus import apply_proportional_downscaling
        stats = apply_proportional_downscaling(db, downscale_factor=0.5)
        row = db.execute("SELECT retired_at FROM memories WHERE id=1").fetchone()
        assert row["retired_at"] is not None
        assert stats["retired"] >= 1

    def test_permanent_memories_exempt(self):
        """Memories with temporal_class='permanent' skip downscaling."""
        db = _make_db()
        _insert_memory(db, content="permanent", confidence=0.8, temporal_class="permanent")
        from agentmemory.hippocampus import apply_proportional_downscaling
        apply_proportional_downscaling(db, downscale_factor=0.5)
        row = db.execute("SELECT confidence FROM memories WHERE id=1").fetchone()
        assert abs(row["confidence"] - 0.8) < 0.001

    def test_tag_cycles_decremented(self):
        """Downscaling pass should decrement tag_cycles_remaining by 1."""
        db = _make_db()
        _insert_memory(db, content="tagged", confidence=0.8, tag_cycles_remaining=3)
        from agentmemory.hippocampus import apply_proportional_downscaling
        apply_proportional_downscaling(db, downscale_factor=0.9)
        row = db.execute("SELECT tag_cycles_remaining FROM memories WHERE id=1").fetchone()
        assert row["tag_cycles_remaining"] == 2


class TestSynapticTagging:
    def test_tag_applied_to_labile_memories(self):
        """Memories in labile window should get tagged."""
        db = _make_db()
        _insert_memory(db, content="labile", confidence=0.5)
        db.execute("""UPDATE memories SET labile_until =
            strftime('%Y-%m-%dT%H:%M:%S', 'now', '+1 hour') WHERE id=1""")
        db.commit()
        from agentmemory.hippocampus import apply_synaptic_tagging
        stats = apply_synaptic_tagging(db, tag_cycles=3)
        row = db.execute("SELECT tag_cycles_remaining FROM memories WHERE id=1").fetchone()
        assert row["tag_cycles_remaining"] == 3

    def test_expired_labile_not_tagged(self):
        """Memories with expired labile windows should not be tagged."""
        db = _make_db()
        _insert_memory(db, content="expired", confidence=0.5)
        db.execute("""UPDATE memories SET labile_until =
            strftime('%Y-%m-%dT%H:%M:%S', 'now', '-1 hour') WHERE id=1""")
        db.commit()
        from agentmemory.hippocampus import apply_synaptic_tagging
        apply_synaptic_tagging(db, tag_cycles=3)
        row = db.execute("SELECT tag_cycles_remaining FROM memories WHERE id=1").fetchone()
        assert row["tag_cycles_remaining"] == 0


class TestSpacingDecay:
    def test_high_stability_decays_slower(self):
        """Memories with high stability should retain more confidence."""
        from agentmemory.hippocampus import compute_spacing_decay
        high_stab = compute_spacing_decay(elapsed_days=30, stability=5.0, rate=0.03)
        low_stab = compute_spacing_decay(elapsed_days=30, stability=1.0, rate=0.03)
        assert high_stab > low_stab

    def test_zero_elapsed_returns_one(self):
        """No time elapsed = no decay."""
        from agentmemory.hippocampus import compute_spacing_decay
        assert compute_spacing_decay(elapsed_days=0, stability=1.0, rate=0.03) == 1.0

    def test_result_bounded_zero_one(self):
        """Decay factor always in [0, 1]."""
        from agentmemory.hippocampus import compute_spacing_decay
        for days in [0, 1, 10, 100, 1000]:
            for stab in [0.1, 1.0, 5.0, 20.0]:
                result = compute_spacing_decay(days, stab, 0.03)
                assert 0.0 <= result <= 1.0

    def test_update_stability_on_spaced_recall(self):
        """Stability should increase when recall is well-spaced."""
        db = _make_db()
        m = _insert_memory(db, stability=1.0)
        from agentmemory.hippocampus import update_memory_stability
        update_memory_stability(db, m, days_since_last_recall=10.0,
                                 temporal_class="medium")
        row = db.execute("SELECT stability FROM memories WHERE id=?", (m,)).fetchone()
        assert row["stability"] > 1.0

    def test_stability_unchanged_on_massed_recall(self):
        """Stability should not increase on rapid repeated recall."""
        db = _make_db()
        m = _insert_memory(db, stability=2.0)
        from agentmemory.hippocampus import update_memory_stability
        update_memory_stability(db, m, days_since_last_recall=0.01,
                                 temporal_class="medium")
        row = db.execute("SELECT stability FROM memories WHERE id=?", (m,)).fetchone()
        assert row["stability"] <= 2.0

    def test_stability_capped_at_twenty(self):
        """Stability should not exceed 20.0."""
        db = _make_db()
        m = _insert_memory(db, stability=18.0)
        from agentmemory.hippocampus import update_memory_stability
        update_memory_stability(db, m, days_since_last_recall=30.0,
                                 temporal_class="medium")
        row = db.execute("SELECT stability FROM memories WHERE id=?", (m,)).fetchone()
        assert row["stability"] <= 20.0


class TestEntityClusteredReplay:
    def test_memories_grouped_by_shared_entity(self):
        """Memories sharing entities should be grouped for replay."""
        db = _make_db()
        m1 = _insert_memory(db, content="Alice likes Python", salience_score=0.8)
        m2 = _insert_memory(db, content="Alice works at Acme", salience_score=0.7)
        m3 = _insert_memory(db, content="Bob likes Java", salience_score=0.6)
        db.execute("INSERT INTO entities (id, name, entity_type) VALUES (1, 'Alice', 'person')")
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id, target_table, target_id,
            relation_type, created_at) VALUES ('memories', ?, 'entities', 1, 'mentions',
            strftime('%Y-%m-%dT%H:%M:%S','now'))""", (m1,))
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id, target_table, target_id,
            relation_type, created_at) VALUES ('memories', ?, 'entities', 1, 'mentions',
            strftime('%Y-%m-%dT%H:%M:%S','now'))""", (m2,))
        db.commit()
        from agentmemory.hippocampus import build_entity_clusters
        clusters = build_entity_clusters(db)
        found_cluster = None
        for cluster in clusters:
            ids = {m["memory_id"] for m in cluster}
            if m1 in ids and m2 in ids:
                found_cluster = cluster
                break
        assert found_cluster is not None
        for cluster in clusters:
            ids = {m["memory_id"] for m in cluster}
            assert m3 not in ids

    def test_magnitude_weighted_selection(self):
        """Higher-salience memories should appear first in replay order."""
        db = _make_db()
        _insert_memory(db, content="low", salience_score=0.2)
        _insert_memory(db, content="high", salience_score=0.9)
        _insert_memory(db, content="mid", salience_score=0.5)
        from agentmemory.hippocampus import select_replay_candidates
        candidates = select_replay_candidates(db, top_k=10)
        assert candidates[0]["salience_score"] >= candidates[1]["salience_score"]

    def test_replay_does_not_change_confidence(self):
        """Replayed memories should not automatically get confidence boost.
        Tagging is a separate step (Widloski & Foster 2025)."""
        db = _make_db()
        m1 = _insert_memory(db, content="replay me", confidence=0.5)
        from agentmemory.hippocampus import replay_memories
        result = replay_memories(db, [{"id": m1, "salience_score": 0.8}])
        row = db.execute("SELECT confidence FROM memories WHERE id=?", (m1,)).fetchone()
        assert abs(row["confidence"] - 0.5) < 0.001
        assert result["replayed"] >= 1

    def test_replay_increments_recalled_count(self):
        """Replay should increment recalled_count."""
        db = _make_db()
        m1 = _insert_memory(db, content="replay me", recalled_count=5)
        from agentmemory.hippocampus import replay_memories
        replay_memories(db, [{"id": m1, "salience_score": 0.8}])
        row = db.execute("SELECT recalled_count FROM memories WHERE id=?", (m1,)).fetchone()
        assert row["recalled_count"] == 6

    def test_empty_candidates_returns_zero(self):
        """Replaying empty list should return replayed=0."""
        db = _make_db()
        from agentmemory.hippocampus import replay_memories
        result = replay_memories(db, [])
        assert result["replayed"] == 0
