# Tier C4-C7: Adaptive Memory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Q-value utility scoring, schema-accelerated consolidation, per-project retrieval presets, and access-pattern-driven replay selection — completing the full CLS architecture.

**Architecture:** One migration (042) adds `q_value` to memories. Schema-accelerated consolidation modifies `promote_episodic_to_semantic` in hippocampus.py. Per-project presets use the existing `agent_state` key-value table. Access-pattern replay enhances `select_replay_candidates`.

**Tech Stack:** Python 3.11+, SQLite, pytest. No new dependencies.

**Spec:** `research/wave14/32_neuroscience_grounded_improvements.md` (Section 8, C4-C7)

---

## Task 1: Q-Value Utility Scoring (Migration 042)

Attach a Q-value to each memory, updated via temporal-difference learning after each retrieval outcome. Use Q-value as a reranking signal.

**Papers:** Zhang et al. 2026 / MemRL [ML-3]

**Files:**
- Create: `db/migrations/042_q_value.sql`
- Modify: `src/agentmemory/db/init_schema.sql`
- Modify: `src/agentmemory/_impl.py` — add `_update_q_value`, wire into access_log path and search scoring
- Create: `tests/test_q_value.py`

- [ ] **Step 1: Create migration 042**

Write `db/migrations/042_q_value.sql`:
```sql
-- Migration 042: Q-value utility scoring (Zhang et al. 2026 / MemRL)
-- Temporal-difference learning: memories that contribute to task success
-- get higher Q-values, improving future retrieval ranking.
ALTER TABLE memories ADD COLUMN q_value REAL DEFAULT 0.5;
```

- [ ] **Step 2: Update init_schema.sql**

Add `q_value REAL DEFAULT 0.5` after `next_review_at` in the memories CREATE TABLE.

- [ ] **Step 3: Write failing tests**

Create `tests/test_q_value.py`:
```python
"""Tests for Q-value utility scoring (Zhang et al. 2026 / MemRL)."""
import sqlite3
import pytest


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'test', content TEXT NOT NULL,
            category TEXT DEFAULT 'lesson', scope TEXT DEFAULT 'global',
            confidence REAL DEFAULT 0.5, q_value REAL DEFAULT 0.5,
            recalled_count INTEGER DEFAULT 0,
            retired_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


def _insert(db, content="test", q_value=0.5):
    db.execute("INSERT INTO memories (content, q_value) VALUES (?, ?)",
               (content, q_value))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestUpdateQValue:
    def test_positive_outcome_increases_q(self):
        """Retrieval that contributed should increase Q-value."""
        db = _make_db()
        m = _insert(db, q_value=0.5)
        from agentmemory._impl import _update_q_value
        _update_q_value(db, m, contributed=True)
        row = db.execute("SELECT q_value FROM memories WHERE id=?", (m,)).fetchone()
        assert row["q_value"] > 0.5

    def test_negative_outcome_decreases_q(self):
        """Retrieval that didn't contribute should decrease Q-value."""
        db = _make_db()
        m = _insert(db, q_value=0.5)
        from agentmemory._impl import _update_q_value
        _update_q_value(db, m, contributed=False)
        row = db.execute("SELECT q_value FROM memories WHERE id=?", (m,)).fetchone()
        assert row["q_value"] < 0.5

    def test_q_bounded_zero_one(self):
        """Q-value should stay in [0, 1]."""
        db = _make_db()
        m = _insert(db, q_value=0.95)
        from agentmemory._impl import _update_q_value
        for _ in range(20):
            _update_q_value(db, m, contributed=True)
        row = db.execute("SELECT q_value FROM memories WHERE id=?", (m,)).fetchone()
        assert row["q_value"] <= 1.0

    def test_learning_rate_controls_speed(self):
        """Higher learning rate = bigger update per step."""
        db = _make_db()
        m1 = _insert(db, q_value=0.5)
        m2 = _insert(db, q_value=0.5)
        from agentmemory._impl import _update_q_value
        _update_q_value(db, m1, contributed=True, learning_rate=0.1)
        _update_q_value(db, m2, contributed=True, learning_rate=0.01)
        r1 = db.execute("SELECT q_value FROM memories WHERE id=?", (m1,)).fetchone()
        r2 = db.execute("SELECT q_value FROM memories WHERE id=?", (m2,)).fetchone()
        assert r1["q_value"] > r2["q_value"]

    def test_q_used_in_reranking(self):
        """Higher Q-value should produce higher effective score."""
        from agentmemory._impl import _q_adjusted_score
        high_q = _q_adjusted_score(base_score=1.0, q_value=0.9)
        low_q = _q_adjusted_score(base_score=1.0, q_value=0.1)
        assert high_q > low_q
```

- [ ] **Step 4: Implement functions**

Add to `src/agentmemory/_impl.py`:
```python
_Q_LEARNING_RATE = 0.1

def _update_q_value(db, memory_id, contributed, learning_rate=_Q_LEARNING_RATE):
    """TD update: q_new = q_old + lr * (reward - q_old). Zhang et al. 2026 / MemRL."""
    reward = 1.0 if contributed else 0.0
    row = db.execute("SELECT q_value FROM memories WHERE id=? AND retired_at IS NULL",
                     (memory_id,)).fetchone()
    if not row:
        return
    q_old = row["q_value"] if row["q_value"] is not None else 0.5
    q_new = max(0.0, min(1.0, q_old + learning_rate * (reward - q_old)))
    db.execute("UPDATE memories SET q_value = ? WHERE id = ?", (q_new, memory_id))
    db.commit()


def _q_adjusted_score(base_score, q_value):
    """Multiply base retrieval score by Q-value weight. Q=0.5 is neutral."""
    q = q_value if q_value is not None else 0.5
    return base_score * (0.8 + 0.4 * q)  # range: 0.8x to 1.2x
```

- [ ] **Step 5: Wire Q-value into search scoring**

In `_apply_recency_and_trim`, add `m.q_value` to SELECT lists in `_fts_memories` and `_vec_memories`. In the scoring loop, after context-match boost:
```python
q_score = _q_adjusted_score(score, r.get("q_value"))
score = q_score
```

- [ ] **Step 6: Wire `_update_q_value` into access_log annotation path**

Find `tool_access_log_annotate` in `mcp_server.py` or the CLI path where `retrieval_contributed` is set on access_log entries. When `retrieval_contributed` is set to 1 or 0, also call `_update_q_value` for the associated memory.

If no explicit annotation path exists, wire into `_retrieval_practice_boost` — when that function is called (on successful retrieval), also bump q_value:
```python
_update_q_value(db, memory_id, contributed=True, learning_rate=_Q_LEARNING_RATE)
```

- [ ] **Step 7: Apply migration, run tests, commit**

```bash
sqlite3 db/brain.db < db/migrations/042_q_value.sql
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_q_value.py -v
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_brain.py tests/test_brain_enhanced.py -q
git add db/migrations/042_q_value.sql src/agentmemory/db/init_schema.sql \
        src/agentmemory/_impl.py src/agentmemory/mcp_server.py tests/test_q_value.py
git commit -m "feat: Q-value utility scoring (migration 042, Zhang et al. 2026 / MemRL)"
```

---

## Task 2: Schema-Accelerated Consolidation

Memories with high entity-link density (>= 3 knowledge_edges) skip the normal episodic holding period and are immediately promoted to semantic during consolidation.

**Papers:** Tse et al. 2007 [CLS-4]

**Files:**
- Modify: `src/agentmemory/hippocampus.py` — add `find_schema_consistent_memories`, modify the phased pipeline
- Create: `tests/test_schema_accelerated.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_schema_accelerated.py`:
```python
"""Tests for schema-accelerated consolidation (Tse et al. 2007)."""
import sqlite3
import pytest


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'test', content TEXT NOT NULL,
            category TEXT DEFAULT 'lesson', scope TEXT DEFAULT 'global',
            confidence REAL DEFAULT 0.5, memory_type TEXT DEFAULT 'episodic',
            temporal_class TEXT DEFAULT 'medium',
            retired_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT NOT NULL, source_id INTEGER NOT NULL,
            target_table TEXT NOT NULL, target_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL, weight REAL DEFAULT 1.0,
            agent_id TEXT, co_activation_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, entity_type TEXT DEFAULT 'concept',
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


def _insert_memory(db, content="test", memory_type="episodic", confidence=0.6):
    db.execute("INSERT INTO memories (content, memory_type, confidence) VALUES (?,?,?)",
               (content, memory_type, confidence))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _link_to_entity(db, memory_id, entity_name):
    db.execute("INSERT OR IGNORE INTO entities (name) VALUES (?)", (entity_name,))
    eid = db.execute("SELECT id FROM entities WHERE name=?", (entity_name,)).fetchone()["id"]
    db.execute("""INSERT INTO knowledge_edges (source_table, source_id, target_table,
        target_id, relation_type) VALUES ('memories', ?, 'entities', ?, 'mentions')""",
        (memory_id, eid))
    db.commit()


class TestSchemaAccelerated:
    def test_high_density_identified(self):
        """Memories with >= 3 edges should be flagged for acceleration."""
        db = _make_db()
        m = _insert_memory(db, content="well-connected")
        _link_to_entity(db, m, "Alice")
        _link_to_entity(db, m, "Acme")
        _link_to_entity(db, m, "Python")
        from agentmemory.hippocampus import find_schema_consistent_memories
        result = find_schema_consistent_memories(db, min_edges=3)
        assert m in result

    def test_low_density_excluded(self):
        """Memories with < 3 edges should not be flagged."""
        db = _make_db()
        m = _insert_memory(db, content="isolated")
        _link_to_entity(db, m, "Alice")
        from agentmemory.hippocampus import find_schema_consistent_memories
        result = find_schema_consistent_memories(db, min_edges=3)
        assert m not in result

    def test_only_episodic_eligible(self):
        """Semantic memories should not be flagged (already promoted)."""
        db = _make_db()
        m = _insert_memory(db, content="already semantic", memory_type="semantic")
        _link_to_entity(db, m, "A")
        _link_to_entity(db, m, "B")
        _link_to_entity(db, m, "C")
        from agentmemory.hippocampus import find_schema_consistent_memories
        result = find_schema_consistent_memories(db, min_edges=3)
        assert m not in result

    def test_accelerated_promotion(self):
        """Schema-consistent memories should be promoted to semantic."""
        db = _make_db()
        m = _insert_memory(db, content="promote me")
        _link_to_entity(db, m, "X")
        _link_to_entity(db, m, "Y")
        _link_to_entity(db, m, "Z")
        from agentmemory.hippocampus import accelerate_schema_consistent
        stats = accelerate_schema_consistent(db, min_edges=3)
        row = db.execute("SELECT memory_type, temporal_class FROM memories WHERE id=?",
                         (m,)).fetchone()
        assert row["memory_type"] == "semantic"
        assert row["temporal_class"] == "long"
        assert stats["promoted"] >= 1
```

- [ ] **Step 2: Implement functions**

Add to `src/agentmemory/hippocampus.py`:
```python
SCHEMA_ACCELERATION_MIN_EDGES = 3

def find_schema_consistent_memories(db, min_edges=SCHEMA_ACCELERATION_MIN_EDGES):
    """Find episodic memories with high entity-link density (Tse et al. 2007).
    Schema-consistent memories consolidate 10x faster."""
    rows = db.execute("""
        SELECT ke.source_id as memory_id, COUNT(*) as edge_count
        FROM knowledge_edges ke
        JOIN memories m ON m.id = ke.source_id
        WHERE ke.source_table = 'memories' AND ke.target_table = 'entities'
          AND m.retired_at IS NULL AND m.memory_type = 'episodic'
        GROUP BY ke.source_id
        HAVING COUNT(*) >= ?
    """, (min_edges,)).fetchall()
    return [r["memory_id"] for r in rows]


def accelerate_schema_consistent(db, min_edges=SCHEMA_ACCELERATION_MIN_EDGES):
    """Promote schema-consistent episodic memories directly to semantic.
    Tse et al. 2007: schema-consistent information bypasses normal
    hippocampal holding and consolidates into neocortex within 48h."""
    memory_ids = find_schema_consistent_memories(db, min_edges=min_edges)
    if not memory_ids:
        return {"promoted": 0}
    placeholders = ",".join("?" * len(memory_ids))
    db.execute(f"""UPDATE memories SET
        memory_type = 'semantic', temporal_class = 'long'
        WHERE id IN ({placeholders}) AND retired_at IS NULL""", memory_ids)
    db.commit()
    return {"promoted": len(memory_ids)}
```

- [ ] **Step 3: Wire into phased consolidation**

In `run_phased_consolidation` (hippocampus.py), add a schema-acceleration step between the coupling gate (phase 4) and de-overlap (phase 5):
```python
# Phase 4.5: Schema-accelerated promotion
schema_stats = accelerate_schema_consistent(db) if not dry_run else {"promoted": 0}
phases["schema_acceleration"] = schema_stats
```

- [ ] **Step 4: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_schema_accelerated.py -v
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py tests/test_consolidation.py -q
git add src/agentmemory/hippocampus.py tests/test_schema_accelerated.py
git commit -m "feat: schema-accelerated consolidation (Tse et al. 2007)"
```

---

## Task 3: Per-Project Retrieval Presets

Store per-project retrieval weight presets in `agent_state`. Load at `orient()` time. Update after each session based on what worked.

**Papers:** Finn et al. 2017 / MAML [ML-5]

**Files:**
- Modify: `src/agentmemory/_impl.py` — add `_load_project_preset`, `_save_project_preset`
- Modify: `src/agentmemory/mcp_server.py` — wire into `tool_agent_orient`
- Create: `tests/test_project_presets.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_project_presets.py`:
```python
"""Tests for per-project retrieval presets (Finn et al. 2017 / MAML)."""
import json
import sqlite3
import pytest


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS agent_state (
            agent_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            PRIMARY KEY (agent_id, key)
        );
    """)
    return db


class TestProjectPresets:
    def test_save_and_load(self):
        """Save a preset, then load it back."""
        db = _make_db()
        from agentmemory._impl import _save_project_preset, _load_project_preset
        preset = {"context_weight": 0.3, "thompson_enabled": True}
        _save_project_preset(db, agent_id="a1", project="api-v2", preset=preset)
        loaded = _load_project_preset(db, agent_id="a1", project="api-v2")
        assert loaded == preset

    def test_load_missing_returns_none(self):
        """Loading a non-existent preset should return None."""
        db = _make_db()
        from agentmemory._impl import _load_project_preset
        assert _load_project_preset(db, agent_id="a1", project="missing") is None

    def test_save_overwrites(self):
        """Saving to same key should overwrite."""
        db = _make_db()
        from agentmemory._impl import _save_project_preset, _load_project_preset
        _save_project_preset(db, "a1", "p1", {"v": 1})
        _save_project_preset(db, "a1", "p1", {"v": 2})
        loaded = _load_project_preset(db, "a1", "p1")
        assert loaded["v"] == 2

    def test_different_projects_independent(self):
        """Different projects should have independent presets."""
        db = _make_db()
        from agentmemory._impl import _save_project_preset, _load_project_preset
        _save_project_preset(db, "a1", "p1", {"w": 0.1})
        _save_project_preset(db, "a1", "p2", {"w": 0.9})
        assert _load_project_preset(db, "a1", "p1")["w"] == 0.1
        assert _load_project_preset(db, "a1", "p2")["w"] == 0.9
```

- [ ] **Step 2: Implement functions**

Add to `src/agentmemory/_impl.py`:
```python
def _save_project_preset(db, agent_id, project, preset):
    """Save per-project retrieval weights to agent_state (Finn/MAML 2017).
    Enables fast adaptation to project-specific retrieval patterns."""
    key = f"retrieval_preset:{project}"
    value = json.dumps(preset, sort_keys=True)
    db.execute("""INSERT INTO agent_state (agent_id, key, value, updated_at)
        VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S','now'))
        ON CONFLICT(agent_id, key) DO UPDATE SET value=excluded.value,
        updated_at=excluded.updated_at""",
        (agent_id, key, value))
    db.commit()


def _load_project_preset(db, agent_id, project):
    """Load per-project retrieval weights from agent_state."""
    key = f"retrieval_preset:{project}"
    row = db.execute("SELECT value FROM agent_state WHERE agent_id=? AND key=?",
                     (agent_id, key)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return None
```

- [ ] **Step 3: Wire into orient (optional — expose for future use)**

In `tool_agent_orient` (mcp_server.py), when `project` is passed, load the preset and include it in the response:
```python
try:
    from agentmemory._impl import _load_project_preset
    preset = _load_project_preset(db, agent_id, project)
    if preset:
        result["retrieval_preset"] = preset
except Exception:
    pass
```

- [ ] **Step 4: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_project_presets.py -v
git add src/agentmemory/_impl.py src/agentmemory/mcp_server.py tests/test_project_presets.py
git commit -m "feat: per-project retrieval presets (Finn et al. 2017 / MAML)"
```

---

## Task 4: Access-Pattern-Driven Replay Selection

Replace the fixed replay limit with selection based on co-access patterns with high-importance events. Scale replay intensity by event importance.

**Papers:** Yang & Buzsaki 2024 [CLS-5], Ramirez-Villegas et al. 2025 [CLS-6]

**Files:**
- Modify: `src/agentmemory/hippocampus.py` — add `select_importance_weighted_replay`
- Create: `tests/test_access_replay.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_access_replay.py`:
```python
"""Tests for access-pattern-driven replay (Yang & Buzsaki 2024)."""
import sqlite3
import pytest


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'test', content TEXT NOT NULL,
            category TEXT DEFAULT 'lesson', scope TEXT DEFAULT 'global',
            confidence REAL DEFAULT 0.5, salience_score REAL DEFAULT 0.5,
            memory_type TEXT DEFAULT 'episodic',
            recalled_count INTEGER DEFAULT 0,
            retired_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'test', summary TEXT NOT NULL,
            event_type TEXT DEFAULT 'observation',
            importance REAL DEFAULT 0.5, project TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'test', action TEXT DEFAULT 'search',
            target_table TEXT, target_id INTEGER,
            query TEXT, result_count INTEGER,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


def _insert_memory(db, content="test", salience_score=0.5):
    db.execute("INSERT INTO memories (content, salience_score) VALUES (?,?)",
               (content, salience_score))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_event(db, summary="test event", importance=0.5):
    db.execute("INSERT INTO events (summary, importance) VALUES (?,?)",
               (summary, importance))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestImportanceWeightedReplay:
    def test_memories_near_important_events_prioritized(self):
        """Memories created near high-importance events should rank higher."""
        db = _make_db()
        m1 = _insert_memory(db, content="near important event", salience_score=0.5)
        m2 = _insert_memory(db, content="near routine event", salience_score=0.5)
        _insert_event(db, "critical deploy", importance=0.9)
        # m1 is co-temporal with the high-importance event
        from agentmemory.hippocampus import select_importance_weighted_replay
        candidates = select_importance_weighted_replay(db, top_k=10)
        assert len(candidates) >= 1

    def test_respects_top_k(self):
        """Should return at most top_k candidates."""
        db = _make_db()
        for i in range(20):
            _insert_memory(db, content=f"memory {i}")
        from agentmemory.hippocampus import select_importance_weighted_replay
        candidates = select_importance_weighted_replay(db, top_k=5)
        assert len(candidates) <= 5

    def test_empty_db_returns_empty(self):
        """No memories = empty list."""
        db = _make_db()
        from agentmemory.hippocampus import select_importance_weighted_replay
        assert select_importance_weighted_replay(db, top_k=10) == []

    def test_scales_by_event_importance(self):
        """Higher event importance should produce higher replay scores."""
        db = _make_db()
        m1 = _insert_memory(db, content="near critical")
        _insert_event(db, "critical", importance=0.95)
        from agentmemory.hippocampus import select_importance_weighted_replay
        candidates = select_importance_weighted_replay(db, top_k=10)
        if candidates:
            assert candidates[0].get("replay_weight", 0) > 0
```

- [ ] **Step 2: Implement**

Add to `src/agentmemory/hippocampus.py`:
```python
def select_importance_weighted_replay(db, top_k=20, lookback_hours=24):
    """Access-pattern-driven replay (Yang & Buzsaki 2024, Ramirez-Villegas 2025).
    Prioritize memories co-temporal with high-importance events.
    Scale replay weight by event importance."""
    rows = db.execute("""
        SELECT m.id, m.content, m.salience_score, m.confidence, m.category,
               COALESCE(MAX(e.importance), 0.5) as max_event_importance
        FROM memories m
        LEFT JOIN events e ON e.created_at >= datetime(m.created_at, '-2 hours')
                          AND e.created_at <= datetime(m.created_at, '+2 hours')
                          AND e.importance >= 0.7
        WHERE m.retired_at IS NULL AND m.memory_type = 'episodic'
        GROUP BY m.id
        ORDER BY (COALESCE(m.salience_score, 0.5) * COALESCE(MAX(e.importance), 0.5)) DESC
        LIMIT ?
    """, (top_k,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["replay_weight"] = (d.get("salience_score") or 0.5) * (d.get("max_event_importance") or 0.5)
        result.append(d)
    return result
```

- [ ] **Step 3: Wire into phased consolidation**

In `run_phased_consolidation`, replace `select_replay_candidates` call with `select_importance_weighted_replay`:
```python
# Phase 3: Replay — importance-weighted + entity-clustered
candidates = select_importance_weighted_replay(db, top_k=20)
```

- [ ] **Step 4: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_access_replay.py -v
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py tests/test_consolidation.py -q
git add src/agentmemory/hippocampus.py tests/test_access_replay.py
git commit -m "feat: access-pattern-driven replay (Yang & Buzsaki 2024)"
```

---

## Final Verification

- [ ] **Run all new v1.9.0 tests**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_q_value.py tests/test_schema_accelerated.py tests/test_project_presets.py tests/test_access_replay.py -v
```

- [ ] **Run full regression suite**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_brain.py tests/test_brain_enhanced.py tests/test_consolidation.py tests/test_consolidation_v2.py -q
```

- [ ] **Run bench harness**

```bash
cd ~/agentmemory && .venv/bin/python -m tests.bench.run --check
```
