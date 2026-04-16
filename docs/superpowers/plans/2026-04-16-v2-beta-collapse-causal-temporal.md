# v2.0-beta: Collapse Dynamics + Causal Edges + Temporal Abstraction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement belief collapse mechanics, typed causal edge attribution, and temporal abstraction hierarchy — the three frontier features completing v2.0.

**Architecture:** Collapse dynamics uses the existing `belief_collapse_events` and `agent_beliefs` tables. Causal edges extend the existing knowledge_edges `causes` type (1,686 edges) with `enables`/`prevents` + counterfactual attribution. Temporal abstraction populates the existing `temporal_level` column and adds a `brainctl abstract` command for hierarchical summarization.

**Tech Stack:** Python 3.11+, SQLite, pytest. No new dependencies or migrations.

**Spec:** `research/wave15/33_v2_roadmap.md` (Pillar 3) + `research/quantum/02_collapse_dynamics.md`

---

## Task 1: Belief Collapse Mechanics

Implement collapse triggers that resolve superposed beliefs into definite states. Four trigger types: task_checkout, direct_query, evidence_threshold, time_decoherence.

**Papers:** Quantum research Wave 2 (02_collapse_dynamics.md)

**Files:**
- Modify: `src/agentmemory/_impl.py` — add `_collapse_belief`, `_check_collapse_triggers`
- Modify: `src/agentmemory/mcp_server.py` — wire into `tool_belief_collapse` if it exists, or add standalone MCP tool
- Create: `tests/test_collapse_dynamics.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_collapse_dynamics.py`:
```python
"""Tests for belief collapse dynamics (Quantum Wave 2)."""
import sqlite3
import math
import pytest


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY, name TEXT, type TEXT DEFAULT 'mcp',
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS agent_beliefs (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            agent_id TEXT NOT NULL REFERENCES agents(id),
            belief_key TEXT NOT NULL,
            belief_value TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            confidence_phase REAL DEFAULT 0.0,
            is_superposed INTEGER DEFAULT 0,
            superposition_values TEXT DEFAULT NULL,
            source TEXT DEFAULT 'observation',
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS belief_collapse_events (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            belief_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            collapsed_state TEXT NOT NULL,
            measured_amplitude REAL NOT NULL,
            collapse_type TEXT NOT NULL,
            collapse_context TEXT DEFAULT NULL,
            collapse_fidelity REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        INSERT INTO agents (id, name) VALUES ('test', 'Test Agent');
    """)
    return db


def _insert_superposed_belief(db, key, values, confidence=0.5):
    import json
    db.execute("""INSERT INTO agent_beliefs
        (agent_id, belief_key, belief_value, confidence, is_superposed,
         superposition_values)
        VALUES ('test', ?, ?, ?, 1, ?)""",
        (key, values[0], confidence, json.dumps(values)))
    db.commit()
    return db.execute("SELECT id FROM agent_beliefs WHERE belief_key=?",
                      (key,)).fetchone()["id"]


class TestCollapseBeliefFunction:
    def test_collapse_resolves_to_single_state(self):
        """After collapse, belief should no longer be superposed."""
        db = _make_db()
        bid = _insert_superposed_belief(db, "deploy_strategy",
            ["blue-green", "canary", "rolling"])
        from agentmemory._impl import _collapse_belief
        result = _collapse_belief(db, belief_id=bid, agent_id="test",
                                   collapse_type="direct_query",
                                   chosen_state="canary")
        row = db.execute("SELECT is_superposed, belief_value FROM agent_beliefs WHERE id=?",
                         (bid,)).fetchone()
        assert row["is_superposed"] == 0
        assert row["belief_value"] == "canary"
        assert result["collapsed_to"] == "canary"

    def test_collapse_logs_event(self):
        """Collapse should create a belief_collapse_events record."""
        db = _make_db()
        bid = _insert_superposed_belief(db, "auth_method", ["JWT", "session"])
        from agentmemory._impl import _collapse_belief
        _collapse_belief(db, bid, "test", "evidence_threshold", "JWT")
        events = db.execute(
            "SELECT * FROM belief_collapse_events WHERE belief_id=?",
            (bid,)).fetchall()
        assert len(events) >= 1
        assert events[0]["collapse_type"] == "evidence_threshold"
        assert events[0]["collapsed_state"] == "JWT"

    def test_collapse_non_superposed_noop(self):
        """Collapsing an already-resolved belief should be a no-op."""
        db = _make_db()
        db.execute("""INSERT INTO agent_beliefs
            (agent_id, belief_key, belief_value, confidence, is_superposed)
            VALUES ('test', 'resolved', 'value', 0.9, 0)""")
        db.commit()
        bid = db.execute("SELECT id FROM agent_beliefs WHERE belief_key='resolved'").fetchone()["id"]
        from agentmemory._impl import _collapse_belief
        result = _collapse_belief(db, bid, "test", "direct_query", "value")
        assert result.get("already_collapsed") is True

    def test_collapse_preserves_pre_state(self):
        """The collapse event should record the measured amplitude."""
        db = _make_db()
        bid = _insert_superposed_belief(db, "framework", ["React", "Vue"], confidence=0.7)
        from agentmemory._impl import _collapse_belief
        _collapse_belief(db, bid, "test", "task_checkout", "React")
        event = db.execute(
            "SELECT measured_amplitude FROM belief_collapse_events WHERE belief_id=?",
            (bid,)).fetchone()
        assert event["measured_amplitude"] > 0


class TestCheckCollapseTriggers:
    def test_time_decoherence_triggers_old_beliefs(self):
        """Superposed beliefs older than threshold should be flagged."""
        db = _make_db()
        db.execute("""INSERT INTO agent_beliefs
            (agent_id, belief_key, belief_value, confidence, is_superposed,
             created_at)
            VALUES ('test', 'old_belief', 'maybe', 0.5, 1,
                    '2026-01-01T00:00:00')""")
        db.commit()
        from agentmemory._impl import _check_collapse_triggers
        candidates = _check_collapse_triggers(db, "test",
            max_superposition_days=30)
        assert len(candidates) >= 1

    def test_recent_beliefs_not_triggered(self):
        """Fresh superposed beliefs should not be triggered by time."""
        db = _make_db()
        _insert_superposed_belief(db, "fresh", ["a", "b"])
        from agentmemory._impl import _check_collapse_triggers
        candidates = _check_collapse_triggers(db, "test",
            max_superposition_days=30)
        assert len(candidates) == 0
```

- [ ] **Step 2: Implement collapse functions**

Add to `src/agentmemory/_impl.py`:
```python
import uuid as _uuid

def _collapse_belief(db, belief_id, agent_id, collapse_type, chosen_state,
                      collapse_context=None):
    """Collapse a superposed belief to a definite state.
    Quantum Wave 2: measurement forces resolution."""
    row = db.execute("SELECT * FROM agent_beliefs WHERE id = ?",
                     (belief_id,)).fetchone()
    if not row:
        return {"error": "belief not found"}
    if not row["is_superposed"]:
        return {"already_collapsed": True, "current_value": row["belief_value"]}

    amplitude = math.sqrt(max(0.0, row["confidence"] or 0.5))

    db.execute("""INSERT INTO belief_collapse_events
        (id, belief_id, agent_id, collapsed_state, measured_amplitude,
         collapse_type, collapse_context)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (_uuid.uuid4().hex, belief_id, agent_id, chosen_state,
         amplitude, collapse_type, collapse_context))

    db.execute("""UPDATE agent_beliefs SET
        is_superposed = 0,
        belief_value = ?,
        superposition_values = NULL,
        updated_at = strftime('%Y-%m-%dT%H:%M:%S', 'now')
        WHERE id = ?""", (chosen_state, belief_id))
    db.commit()

    return {"collapsed_to": chosen_state, "amplitude": amplitude,
            "collapse_type": collapse_type}


def _check_collapse_triggers(db, agent_id, max_superposition_days=30):
    """Check for superposed beliefs that should be collapsed.
    Time decoherence: beliefs superposed longer than threshold."""
    candidates = db.execute("""
        SELECT id, belief_key, belief_value, confidence,
               superposition_values, created_at
        FROM agent_beliefs
        WHERE agent_id = ? AND is_superposed = 1
          AND julianday('now') - julianday(created_at) > ?
    """, (agent_id, max_superposition_days)).fetchall()
    return [dict(r) for r in candidates]
```

- [ ] **Step 3: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_collapse_dynamics.py -v
git add src/agentmemory/_impl.py tests/test_collapse_dynamics.py
git commit -m "feat: belief collapse dynamics (Quantum Wave 2)

_collapse_belief: resolves superposed beliefs to definite states.
Logs collapse events with amplitude, type, and context.
_check_collapse_triggers: finds beliefs overdue for time decoherence.
Four collapse types: task_checkout, direct_query, evidence_threshold,
time_decoherence."
```

---

## Task 2: Typed Causal Edges + Counterfactual Attribution

Extend the existing 'causes' edges (1,686) with 'enables' and 'prevents' types. Add a counterfactual attribution helper that traces which memories contributed to outcomes.

**Papers:** Kang et al. (2025) Hindsight, arXiv:2512.12818

**Files:**
- Modify: `src/agentmemory/_impl.py` — add `_add_causal_edge`, `_trace_causal_chain`, `_counterfactual_attribution`
- Create: `tests/test_causal_edges.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_causal_edges.py`:
```python
"""Tests for typed causal edges (Kang et al. 2025 / Hindsight)."""
import sqlite3
import pytest


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL, confidence REAL DEFAULT 0.5,
            q_value REAL DEFAULT 0.5,
            retired_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary TEXT NOT NULL, event_type TEXT DEFAULT 'result',
            importance REAL DEFAULT 0.5,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT NOT NULL, source_id INTEGER NOT NULL,
            target_table TEXT NOT NULL, target_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL, weight REAL DEFAULT 1.0,
            agent_id TEXT DEFAULT 'test',
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            UNIQUE(source_table, source_id, target_table, target_id, relation_type)
        );
    """)
    return db


class TestAddCausalEdge:
    def test_creates_typed_edge(self):
        """Should create a knowledge_edge with the specified causal type."""
        db = _make_db()
        db.execute("INSERT INTO memories (content) VALUES ('memory A')")
        db.execute("INSERT INTO events (summary) VALUES ('outcome B')")
        db.commit()
        from agentmemory._impl import _add_causal_edge
        _add_causal_edge(db, source_table="memories", source_id=1,
                          target_table="events", target_id=1,
                          causal_type="enables", weight=0.8)
        edge = db.execute("""SELECT * FROM knowledge_edges
            WHERE relation_type='enables'""").fetchone()
        assert edge is not None
        assert edge["weight"] == 0.8

    def test_valid_causal_types(self):
        """Only causes/enables/prevents should be accepted."""
        db = _make_db()
        db.execute("INSERT INTO memories (content) VALUES ('a')")
        db.execute("INSERT INTO memories (content) VALUES ('b')")
        db.commit()
        from agentmemory._impl import _add_causal_edge
        result = _add_causal_edge(db, "memories", 1, "memories", 2,
                                   causal_type="invalid_type")
        assert result.get("error") is not None


class TestTraceCausalChain:
    def test_follows_chain(self):
        """Should trace a causal chain through knowledge_edges."""
        db = _make_db()
        for i in range(4):
            db.execute("INSERT INTO memories (content) VALUES (?)", (f"step {i}",))
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id,
            target_table, target_id, relation_type)
            VALUES ('memories', 1, 'memories', 2, 'causes')""")
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id,
            target_table, target_id, relation_type)
            VALUES ('memories', 2, 'memories', 3, 'causes')""")
        db.commit()
        from agentmemory._impl import _trace_causal_chain
        chain = _trace_causal_chain(db, start_table="memories", start_id=1,
                                     max_hops=5)
        assert len(chain) >= 2  # at least 2 hops

    def test_respects_max_hops(self):
        """Should stop at max_hops."""
        db = _make_db()
        for i in range(10):
            db.execute("INSERT INTO memories (content) VALUES (?)", (f"m{i}",))
        for i in range(9):
            db.execute("""INSERT INTO knowledge_edges (source_table, source_id,
                target_table, target_id, relation_type)
                VALUES ('memories', ?, 'memories', ?, 'causes')""", (i+1, i+2))
        db.commit()
        from agentmemory._impl import _trace_causal_chain
        chain = _trace_causal_chain(db, "memories", 1, max_hops=3)
        assert len(chain) <= 3


class TestCounterfactualAttribution:
    def test_attributes_contributing_memories(self):
        """Memories in the causal chain of a positive outcome should
        get Q-value boosts."""
        db = _make_db()
        db.execute("INSERT INTO memories (content, q_value) VALUES ('cause', 0.5)")
        db.execute("INSERT INTO events (summary, event_type, importance) VALUES ('success', 'result', 0.9)")
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id,
            target_table, target_id, relation_type)
            VALUES ('memories', 1, 'events', 1, 'causes')""")
        db.commit()
        from agentmemory._impl import _counterfactual_attribution
        stats = _counterfactual_attribution(db, event_id=1, outcome_positive=True)
        row = db.execute("SELECT q_value FROM memories WHERE id=1").fetchone()
        assert row["q_value"] > 0.5
        assert stats["memories_attributed"] >= 1
```

- [ ] **Step 2: Implement**

```python
_VALID_CAUSAL_TYPES = {"causes", "enables", "prevents"}

def _add_causal_edge(db, source_table, source_id, target_table, target_id,
                      causal_type, weight=1.0, agent_id="causal"):
    """Add a typed causal edge (Kang et al. 2025 / Hindsight)."""
    if causal_type not in _VALID_CAUSAL_TYPES:
        return {"error": f"invalid causal_type: {causal_type}. Must be one of {_VALID_CAUSAL_TYPES}"}
    try:
        db.execute("""INSERT OR IGNORE INTO knowledge_edges
            (source_table, source_id, target_table, target_id,
             relation_type, weight, agent_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?,
                    strftime('%Y-%m-%dT%H:%M:%S','now'))""",
            (source_table, source_id, target_table, target_id,
             causal_type, weight, agent_id))
        db.commit()
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


def _trace_causal_chain(db, start_table, start_id, max_hops=5):
    """Trace a causal chain forward through knowledge_edges.
    Follows causes/enables edges up to max_hops."""
    chain = []
    current_table, current_id = start_table, start_id
    visited = set()
    for _ in range(max_hops):
        key = (current_table, current_id)
        if key in visited:
            break
        visited.add(key)
        next_hop = db.execute("""
            SELECT target_table, target_id, relation_type, weight
            FROM knowledge_edges
            WHERE source_table = ? AND source_id = ?
              AND relation_type IN ('causes', 'enables')
            ORDER BY weight DESC LIMIT 1
        """, (current_table, current_id)).fetchone()
        if not next_hop:
            break
        chain.append(dict(next_hop))
        current_table = next_hop["target_table"]
        current_id = next_hop["target_id"]
    return chain


def _counterfactual_attribution(db, event_id, outcome_positive=True):
    """Counterfactual attribution (Hindsight, Kang et al. 2025).
    Trace backward from an outcome event, boost Q-values of
    contributing memories."""
    backward = db.execute("""
        SELECT source_table, source_id, weight FROM knowledge_edges
        WHERE target_table = 'events' AND target_id = ?
          AND relation_type IN ('causes', 'enables')
    """, (event_id,)).fetchall()

    lr = 0.1
    reward = 1.0 if outcome_positive else 0.0
    attributed = 0

    for edge in backward:
        if edge["source_table"] == "memories":
            row = db.execute("SELECT q_value FROM memories WHERE id=? AND retired_at IS NULL",
                             (edge["source_id"],)).fetchone()
            if row:
                q_old = row["q_value"] or 0.5
                q_new = max(0.0, min(1.0, q_old + lr * edge["weight"] * (reward - q_old)))
                db.execute("UPDATE memories SET q_value = ? WHERE id = ?",
                           (q_new, edge["source_id"]))
                attributed += 1
    db.commit()
    return {"memories_attributed": attributed, "outcome_positive": outcome_positive}
```

- [ ] **Step 3: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_causal_edges.py -v
git add src/agentmemory/_impl.py tests/test_causal_edges.py
git commit -m "feat: typed causal edges + counterfactual attribution (Hindsight)

_add_causal_edge: creates causes/enables/prevents edges.
_trace_causal_chain: follows causal chains up to max_hops.
_counterfactual_attribution: traces backward from outcomes, boosts
Q-values of contributing memories weighted by edge weight.

Papers: Kang et al. (2025) Hindsight, arXiv:2512.12818"
```

---

## Task 3: Temporal Abstraction Hierarchy

Populate the `temporal_level` column from temporal_class mapping and add a `brainctl abstract` command that builds hierarchical summaries (session → day → week → month).

**Papers:** Shu et al. (2025) TiMem, arXiv:2601.02845

**Files:**
- Modify: `src/agentmemory/_impl.py` — add `_assign_temporal_levels`, `_build_temporal_summary`
- Create: `tests/test_temporal_abstraction.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_temporal_abstraction.py`:
```python
"""Tests for temporal abstraction hierarchy (Shu et al. 2025 / TiMem)."""
import sqlite3
import pytest
from datetime import datetime, timedelta


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'test', content TEXT NOT NULL,
            category TEXT DEFAULT 'lesson', scope TEXT DEFAULT 'global',
            confidence REAL DEFAULT 0.5, temporal_class TEXT DEFAULT 'medium',
            temporal_level TEXT DEFAULT 'moment',
            memory_type TEXT DEFAULT 'episodic',
            retired_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


def _insert_memory(db, content, created_at=None, temporal_level="moment"):
    if created_at:
        db.execute("""INSERT INTO memories (content, temporal_level, created_at, updated_at)
            VALUES (?, ?, ?, ?)""", (content, temporal_level, created_at, created_at))
    else:
        db.execute("INSERT INTO memories (content, temporal_level) VALUES (?, ?)",
                   (content, temporal_level))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestAssignTemporalLevels:
    def test_assigns_based_on_age(self):
        """Memories should get level based on age: recent=moment,
        older=session, oldest=day/week/month."""
        db = _make_db()
        now = datetime.now()
        _insert_memory(db, "just now", created_at=now.strftime("%Y-%m-%dT%H:%M:%S"))
        _insert_memory(db, "yesterday",
            created_at=(now - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%S"))
        _insert_memory(db, "last week",
            created_at=(now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S"))
        _insert_memory(db, "last month",
            created_at=(now - timedelta(days=25)).strftime("%Y-%m-%dT%H:%M:%S"))
        from agentmemory._impl import _assign_temporal_levels
        stats = _assign_temporal_levels(db)
        assert stats["updated"] >= 1

    def test_levels_are_valid(self):
        """All assigned levels should be from the valid set."""
        db = _make_db()
        now = datetime.now()
        for days in [0, 1, 3, 7, 14, 30, 60, 90]:
            _insert_memory(db, f"{days} days ago",
                created_at=(now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S"))
        from agentmemory._impl import _assign_temporal_levels
        _assign_temporal_levels(db)
        rows = db.execute("SELECT DISTINCT temporal_level FROM memories").fetchall()
        valid = {"moment", "session", "day", "week", "month", "quarter"}
        for r in rows:
            assert r["temporal_level"] in valid


class TestBuildTemporalSummary:
    def test_summarizes_day_from_moments(self):
        """Should produce a day-level summary from moment-level memories."""
        db = _make_db()
        date = "2026-04-15"
        for i in range(5):
            _insert_memory(db, f"Did task {i} on April 15",
                created_at=f"{date}T{10+i}:00:00", temporal_level="moment")
        from agentmemory._impl import _build_temporal_summary
        result = _build_temporal_summary(db, level="day", date=date)
        assert result["memories_summarized"] >= 1
        assert result.get("summary_content") is not None

    def test_empty_day_returns_none(self):
        """Day with no memories should return empty summary."""
        db = _make_db()
        from agentmemory._impl import _build_temporal_summary
        result = _build_temporal_summary(db, level="day", date="2025-01-01")
        assert result["memories_summarized"] == 0
```

- [ ] **Step 2: Implement**

```python
_TEMPORAL_LEVEL_THRESHOLDS = [
    (0.5, "moment"),     # < 12 hours
    (1.0, "session"),    # 12h - 1 day
    (7.0, "day"),        # 1-7 days
    (30.0, "week"),      # 7-30 days
    (90.0, "month"),     # 30-90 days
]

def _assign_temporal_levels(db):
    """Assign temporal_level based on memory age (TiMem, Shu et al. 2025).
    5-level hierarchy: moment → session → day → week → month → quarter."""
    updated = 0
    rows = db.execute("""
        SELECT id, julianday('now') - julianday(created_at) as age_days
        FROM memories WHERE retired_at IS NULL
    """).fetchall()
    for r in rows:
        age = r["age_days"] or 0
        level = "quarter"
        for threshold, lvl in _TEMPORAL_LEVEL_THRESHOLDS:
            if age < threshold:
                level = lvl
                break
        db.execute("UPDATE memories SET temporal_level = ? WHERE id = ?",
                   (level, r["id"]))
        updated += 1
    db.commit()
    return {"updated": updated}


def _build_temporal_summary(db, level="day", date=None, agent_id="test"):
    """Build a hierarchical summary at the specified temporal level.
    TiMem: 52% memory length reduction via hierarchical compression."""
    if level == "day" and date:
        rows = db.execute("""
            SELECT id, content FROM memories
            WHERE retired_at IS NULL
              AND date(created_at) = ?
              AND temporal_level = 'moment'
            ORDER BY created_at
        """, (date,)).fetchall()
    elif level == "week" and date:
        rows = db.execute("""
            SELECT id, content FROM memories
            WHERE retired_at IS NULL
              AND date(created_at) >= date(?, '-7 days')
              AND date(created_at) <= ?
              AND temporal_level IN ('moment', 'session')
            ORDER BY created_at
        """, (date, date)).fetchall()
    else:
        rows = []

    if not rows:
        return {"memories_summarized": 0, "summary_content": None}

    contents = [r["content"] for r in rows]
    summary = "; ".join(c[:100] for c in contents[:20])

    return {
        "memories_summarized": len(rows),
        "summary_content": summary,
        "level": level,
        "date": date,
        "source_ids": [r["id"] for r in rows],
    }
```

- [ ] **Step 3: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_temporal_abstraction.py -v
git add src/agentmemory/_impl.py tests/test_temporal_abstraction.py
git commit -m "feat: temporal abstraction hierarchy (TiMem, Shu et al. 2025)

_assign_temporal_levels: 5-level hierarchy based on memory age
(moment → session → day → week → month → quarter).
_build_temporal_summary: hierarchical summarization for day/week.
TiMem shows 52% memory length reduction via temporal compression.

Papers: Shu et al. (2025) TiMem, arXiv:2601.02845"
```

---

## Final Verification

- [ ] **Run all new tests**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_collapse_dynamics.py tests/test_causal_edges.py tests/test_temporal_abstraction.py -v
```

- [ ] **Run full regression suite**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_brain.py tests/test_brain_enhanced.py tests/test_consolidation_v2.py -q
```

- [ ] **Run temporal level assignment on real brain.db**

```bash
cd ~/agentmemory && .venv/bin/python -c "
import sqlite3, json
from agentmemory._impl import _assign_temporal_levels
db = sqlite3.connect('db/brain.db')
db.row_factory = sqlite3.Row
result = _assign_temporal_levels(db)
print(json.dumps(result))
levels = db.execute('SELECT temporal_level, COUNT(*) as c FROM memories WHERE retired_at IS NULL GROUP BY temporal_level ORDER BY c DESC').fetchall()
for r in levels:
    print(f'  {r[\"temporal_level\"]}: {r[\"c\"]}')
"
```
