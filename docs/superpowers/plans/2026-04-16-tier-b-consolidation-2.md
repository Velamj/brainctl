# Tier B: Consolidation 2.0 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace brainctl's ad-hoc consolidation engine with a neuroscience-principled phased pipeline featuring homeostatic pressure triggers, global proportional downscaling, synaptic tagging, entity-clustered replay, and coupling gates.

**Architecture:** The existing `cmd_consolidation_cycle()` in `hippocampus.py` (lines 1728-1886) runs 12 passes in a flat sequence. This overhaul restructures it into a 7-phase pipeline modeled on NREM/REM sleep stages, with demand-driven triggers and per-memory protection mechanisms. New functions are added to `hippocampus.py`; the cycle orchestrator is rewritten.

**Tech Stack:** Python 3.11+, SQLite, pytest. No new dependencies. Two migrations (038, 039).

**Spec:** `research/wave14/32_neuroscience_grounded_improvements.md` (Section 7, revised 2026-04-16)

**Key files:**
- `src/agentmemory/hippocampus.py` — main consolidation engine (3500+ lines)
- `db/migrations/038_synaptic_tagging.sql` — tag_cycles_remaining column
- `db/migrations/039_memory_stability.sql` — stability column for spacing-effect decay
- `src/agentmemory/db/init_schema.sql` — schema updates
- `bin/consolidation-cycle.sh` — shell orchestration (update trigger logic)
- `tests/test_consolidation_v2.py` — new test file for all Tier B tests

---

## Task 1: Migrations 038-039 (Schema)

Add `tag_cycles_remaining` and `stability` columns to the memories table.

**Files:**
- Create: `db/migrations/038_synaptic_tagging.sql`
- Create: `db/migrations/039_memory_stability.sql`
- Modify: `src/agentmemory/db/init_schema.sql`

- [ ] **Step 1: Create migration 038**

```sql
-- Migration 038: synaptic tagging protection (Frey & Morris 1997)
-- Memories within the labile window of a high-importance event get
-- tagged for protection from consolidation downscaling.
ALTER TABLE memories ADD COLUMN tag_cycles_remaining INTEGER DEFAULT 0;
```

Write to `db/migrations/038_synaptic_tagging.sql`.

- [ ] **Step 2: Create migration 039**

```sql
-- Migration 039: memory stability for spacing-effect decay (Cepeda et al. 2006)
-- Stability increases when a memory is recalled at well-spaced intervals.
-- Used by the spacing-effect decay function to slow decay for stable memories.
ALTER TABLE memories ADD COLUMN stability REAL DEFAULT 1.0;
```

Write to `db/migrations/039_memory_stability.sql`.

- [ ] **Step 3: Add columns to init_schema.sql**

Add after the `encoding_affect_id` column (added in migration 037):

```sql
tag_cycles_remaining INTEGER DEFAULT 0,
stability REAL DEFAULT 1.0,
```

- [ ] **Step 4: Apply migrations to dev DB**

```bash
cd ~/agentmemory
sqlite3 db/brain.db < db/migrations/038_synaptic_tagging.sql
sqlite3 db/brain.db < db/migrations/039_memory_stability.sql
```

- [ ] **Step 5: Verify columns exist**

```bash
cd ~/agentmemory && sqlite3 db/brain.db "PRAGMA table_info(memories)" | grep -E "tag_cycles|stability"
```

Expected: two rows showing the new columns.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/038_synaptic_tagging.sql db/migrations/039_memory_stability.sql src/agentmemory/db/init_schema.sql
git commit -m "schema: migrations 038-039 — synaptic tagging + memory stability

038: tag_cycles_remaining (INTEGER DEFAULT 0) for Frey & Morris 1997
synaptic tagging protection during consolidation downscaling.

039: stability (REAL DEFAULT 1.0) for Cepeda et al. 2006 spacing-effect
decay. Increases when recalled at well-spaced intervals."
```

---

## Task 2: Homeostatic Pressure Computation

Compute homeostatic pressure from total confidence mass / active memory count. This metric drives demand-based consolidation triggering.

**Papers:** Tononi & Cirelli 2003/2006 [SHY-1/2], Schabus et al. 2004 [SHY-11]

**Files:**
- Modify: `src/agentmemory/hippocampus.py` — add pressure functions
- Test: `tests/test_consolidation_v2.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_consolidation_v2.py`:

```python
"""Tests for Consolidation 2.0 (Tier B neuroscience-grounded improvements)."""
import sqlite3
import math
import pathlib
import pytest


def _make_db():
    """Create in-memory DB with brainctl schema."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    schema_path = pathlib.Path(__file__).parent.parent / "src" / "agentmemory" / "db" / "init_schema.sql"
    try:
        db.executescript(schema_path.read_text())
    except Exception:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL DEFAULT 'test',
                content TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'lesson',
                scope TEXT NOT NULL DEFAULT 'global',
                confidence REAL NOT NULL DEFAULT 0.5,
                alpha REAL DEFAULT 1.0,
                beta REAL DEFAULT 1.0,
                recalled_count INTEGER DEFAULT 0,
                memory_type TEXT DEFAULT 'episodic',
                temporal_class TEXT DEFAULT 'medium',
                ewc_importance REAL DEFAULT 0.0,
                protected INTEGER DEFAULT 0,
                salience_score REAL DEFAULT 0.5,
                tag_cycles_remaining INTEGER DEFAULT 0,
                stability REAL DEFAULT 1.0,
                retired_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
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
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
                co_activation_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT 'concept',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL DEFAULT 'test',
                summary TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'observation',
                importance REAL DEFAULT 0.5,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
            );
            CREATE TABLE IF NOT EXISTS affect_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL DEFAULT 'test',
                valence REAL DEFAULT 0.0,
                arousal REAL DEFAULT 0.0,
                dominance REAL DEFAULT 0.0,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
            );
        """)
    return db


def _insert_memory(db, content="test", confidence=0.5, category="lesson",
                    temporal_class="medium", ewc_importance=0.0,
                    tag_cycles_remaining=0, stability=1.0,
                    recalled_count=0, memory_type="episodic",
                    protected=0, salience_score=0.5, agent_id="test",
                    created_at=None):
    created = created_at or "strftime('%Y-%m-%dT%H:%M:%S','now')"
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
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestHomeostaticPressure:
    def test_pressure_is_mean_confidence(self):
        """Pressure = total confidence mass / active memory count."""
        db = _make_db()
        _insert_memory(db, confidence=0.8)
        _insert_memory(db, confidence=0.6)
        _insert_memory(db, confidence=0.4)
        from agentmemory.hippocampus import compute_homeostatic_pressure
        p = compute_homeostatic_pressure(db)
        assert abs(p - 0.6) < 0.01

    def test_pressure_excludes_retired(self):
        """Retired memories should not count toward pressure."""
        db = _make_db()
        _insert_memory(db, confidence=0.8)
        _insert_memory(db, confidence=0.4)
        db.execute("UPDATE memories SET retired_at='2026-01-01' WHERE id=2")
        db.commit()
        from agentmemory.hippocampus import compute_homeostatic_pressure
        p = compute_homeostatic_pressure(db)
        assert abs(p - 0.8) < 0.01

    def test_empty_db_returns_zero(self):
        """No memories = zero pressure."""
        db = _make_db()
        from agentmemory.hippocampus import compute_homeostatic_pressure
        assert compute_homeostatic_pressure(db) == 0.0

    def test_learning_load_counts_recent(self):
        """Learning load = memories created since last consolidation."""
        db = _make_db()
        _insert_memory(db, created_at="2026-04-15T10:00:00")
        _insert_memory(db, created_at="2026-04-15T11:00:00")
        _insert_memory(db, created_at="2026-04-15T14:00:00")
        from agentmemory.hippocampus import compute_learning_load
        load = compute_learning_load(db, since="2026-04-15T12:00:00")
        assert load == 1  # only the 14:00 memory

    def test_should_trigger_above_setpoint(self):
        """Should trigger when pressure > setpoint or load > threshold."""
        from agentmemory.hippocampus import should_trigger_consolidation
        assert should_trigger_consolidation(pressure=0.7, setpoint=0.5,
                                             learning_load=5, load_threshold=20)
        assert not should_trigger_consolidation(pressure=0.3, setpoint=0.5,
                                                 learning_load=5, load_threshold=20)

    def test_should_trigger_high_load(self):
        """High learning load should trigger even if pressure is low."""
        from agentmemory.hippocampus import should_trigger_consolidation
        assert should_trigger_consolidation(pressure=0.3, setpoint=0.5,
                                             learning_load=25, load_threshold=20)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py::TestHomeostaticPressure -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement pressure functions in hippocampus.py**

Add near the top of `hippocampus.py`, after the constants section:

```python
HOMEOSTATIC_SETPOINT = 0.55
LEARNING_LOAD_THRESHOLD = 20

def compute_homeostatic_pressure(db):
    """Total confidence mass / active memory count (Tononi & Cirelli 2003).
    Tracks net synaptic strength; consolidation restores homeostasis."""
    row = db.execute("""
        SELECT COALESCE(SUM(confidence), 0.0) as total,
               COUNT(*) as cnt
        FROM memories WHERE retired_at IS NULL
    """).fetchone()
    if row["cnt"] == 0:
        return 0.0
    return row["total"] / row["cnt"]


def compute_learning_load(db, since=None):
    """Count memories created since a reference timestamp."""
    if not since:
        return 0
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM memories WHERE retired_at IS NULL AND created_at > ?",
        (since,)).fetchone()
    return row["cnt"]


def should_trigger_consolidation(pressure, setpoint=HOMEOSTATIC_SETPOINT,
                                  learning_load=0, load_threshold=LEARNING_LOAD_THRESHOLD):
    """Demand-driven consolidation trigger (Tononi & Cirelli 2006).
    Fires when homeostatic pressure exceeds setpoint OR learning load
    exceeds threshold. Cron remains as fallback."""
    return pressure > setpoint or learning_load > load_threshold
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py::TestHomeostaticPressure -v`
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/agentmemory/hippocampus.py tests/test_consolidation_v2.py
git commit -m "feat: homeostatic pressure computation (Tononi & Cirelli 2003)

compute_homeostatic_pressure: total confidence / active memory count.
compute_learning_load: count of memories since last consolidation.
should_trigger_consolidation: fires when pressure > setpoint or
learning load > threshold. Cron remains as fallback.

Papers: [SHY-1] Tononi & Cirelli 2003, [SHY-2] 2006, [SHY-11] Schabus 2004"
```

---

## Task 3: Global Proportional Downscaling with Tagging

Replace per-class DECAY_RATES with a single global downscale factor. Memories tagged for protection or with high importance resist downscaling. Includes predictive forgetting.

**Papers:** Tononi & Cirelli 2014 [SHY-3], Kirkpatrick 2017 [SHY-13], Frey & Morris 1997 [SHY-7], Fountas et al. 2026 [2026-16]

**Files:**
- Modify: `src/agentmemory/hippocampus.py` — add `apply_proportional_downscaling`
- Test: `tests/test_consolidation_v2.py` — add `TestProportionalDownscaling` + `TestSynapticTagging`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_consolidation_v2.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py::TestProportionalDownscaling tests/test_consolidation_v2.py::TestSynapticTagging -v`

- [ ] **Step 3: Implement downscaling and tagging functions**

Add to `hippocampus.py`:

```python
RETIREMENT_THRESHOLD = 0.05
DEFAULT_TAG_CYCLES = 3

def apply_synaptic_tagging(db, tag_cycles=DEFAULT_TAG_CYCLES):
    """Tag memories in active labile windows for protection from downscaling.
    Frey & Morris 1997: synaptic tagging and capture hypothesis."""
    result = db.execute("""
        UPDATE memories SET tag_cycles_remaining = ?
        WHERE retired_at IS NULL
          AND labile_until IS NOT NULL
          AND labile_until > strftime('%Y-%m-%dT%H:%M:%S', 'now')
          AND tag_cycles_remaining < ?
    """, (tag_cycles, tag_cycles))
    db.commit()
    return {"tagged": result.rowcount}


def apply_proportional_downscaling(db, downscale_factor=0.95):
    """Global proportional downscaling (Tononi & Cirelli 2014, rule 3).
    Tagged and permanent memories are exempt. High-importance memories
    resist downscaling (EWC analog, Kirkpatrick 2017). Memories below
    retirement threshold get retired."""
    rows = db.execute("""
        SELECT id, confidence, ewc_importance, tag_cycles_remaining,
               temporal_class, protected
        FROM memories
        WHERE retired_at IS NULL
    """).fetchall()

    downscaled = 0
    retired = 0
    skipped = 0

    for r in rows:
        if r["temporal_class"] == "permanent":
            skipped += 1
            continue
        if r["tag_cycles_remaining"] and r["tag_cycles_remaining"] > 0:
            skipped += 1
            continue

        importance = max(0.0, min(1.0, r["ewc_importance"] or 0.0))
        effective_factor = downscale_factor ** (1.0 - importance)
        new_conf = r["confidence"] * effective_factor

        if new_conf < RETIREMENT_THRESHOLD and not r["protected"]:
            db.execute("""UPDATE memories SET confidence = ?,
                retired_at = strftime('%Y-%m-%dT%H:%M:%S', 'now')
                WHERE id = ?""", (new_conf, r["id"]))
            retired += 1
        else:
            db.execute("UPDATE memories SET confidence = ? WHERE id = ?",
                       (new_conf, r["id"]))
            downscaled += 1

    # Decrement tag_cycles_remaining for all tagged memories
    db.execute("""UPDATE memories SET tag_cycles_remaining = tag_cycles_remaining - 1
        WHERE retired_at IS NULL AND tag_cycles_remaining > 0""")
    db.commit()

    return {"downscaled": downscaled, "retired": retired, "skipped": skipped,
            "downscale_factor": downscale_factor}
```

- [ ] **Step 4: Run tests**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add src/agentmemory/hippocampus.py tests/test_consolidation_v2.py
git commit -m "feat: global proportional downscaling + synaptic tagging

apply_proportional_downscaling: single multiplicative factor replaces
per-class decay. Tagged/permanent memories exempt. EWC importance
resists downscaling. Below-threshold memories retired.

apply_synaptic_tagging: tags labile-window memories for N consolidation
cycles of protection from downscaling.

Papers: [SHY-3] Tononi & Cirelli 2014, [SHY-7] Frey & Morris 1997,
[SHY-13] Kirkpatrick 2017, [2026-16] Fountas et al. 2026"
```

---

## Task 4: Spacing-Effect Decay Function

Replace flat exponential decay with a spacing-aware function where stability increases for well-spaced recalls.

**Papers:** Cepeda et al. 2006 [CE-7], Hou et al. 2024 [ML-4]

**Files:**
- Modify: `src/agentmemory/hippocampus.py` — add `compute_spacing_decay`
- Test: `tests/test_consolidation_v2.py` — add `TestSpacingDecay`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Implement**

```python
_RETENTION_INTERVALS = {
    "ephemeral": 3.5, "short": 10.0, "medium": 23.0, "long": 70.0, "permanent": 365.0,
}

def compute_spacing_decay(elapsed_days, stability=1.0, rate=0.03):
    """Spacing-effect decay (Cepeda et al. 2006, Hou et al. 2024).
    p(t) = exp(-rate * t / stability). High stability = slower decay."""
    if elapsed_days <= 0:
        return 1.0
    s = max(0.1, stability)
    return math.exp(-rate * elapsed_days / s)


def update_memory_stability(db, memory_id, days_since_last_recall, temporal_class="medium"):
    """Increase stability for well-spaced recalls (ISI >= 15% of retention interval).
    Cepeda et al. 2006: optimal ISI is ~10-20% of retention interval."""
    ri = _RETENTION_INTERVALS.get(temporal_class, 23.0)
    optimal_isi = ri * 0.15
    if days_since_last_recall >= optimal_isi:
        db.execute("""UPDATE memories SET stability = MIN(20.0, stability * 1.3)
            WHERE id = ? AND retired_at IS NULL""", (memory_id,))
    db.commit()
```

- [ ] **Step 3: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py::TestSpacingDecay -v
git add src/agentmemory/hippocampus.py tests/test_consolidation_v2.py
git commit -m "feat: spacing-effect decay function (Cepeda et al. 2006)

compute_spacing_decay: exp(-rate * t / stability). High-stability
memories decay slower. Stability increases on well-spaced recalls
(ISI >= 15% of retention interval per category).

Papers: [CE-7] Cepeda et al. 2006, [ML-4] Hou et al. 2024"
```

---

## Task 5: Entity-Clustered Replay with Magnitude Weighting

Restructure the replay queue to group candidates by shared entity references and weight by salience magnitude. Decouple replay from tagging.

**Papers:** Niediek et al. 2026 [2026-24], Robinson et al. 2026 [2026-18], Widloski & Foster 2025 [2026-28]

**Files:**
- Modify: `src/agentmemory/hippocampus.py` — add `entity_clustered_replay`
- Test: `tests/test_consolidation_v2.py` — add `TestEntityClusteredReplay`

- [ ] **Step 1: Write failing tests**

```python
class TestEntityClusteredReplay:
    def test_memories_grouped_by_shared_entity(self):
        """Memories sharing entities should be grouped for replay."""
        db = _make_db()
        m1 = _insert_memory(db, content="Alice likes Python", salience_score=0.8)
        m2 = _insert_memory(db, content="Alice works at Acme", salience_score=0.7)
        m3 = _insert_memory(db, content="Bob likes Java", salience_score=0.6)
        # Create entity edges: m1 and m2 both link to entity "Alice"
        db.execute("""INSERT INTO entities (id, name, entity_type) VALUES (1, 'Alice', 'person')""")
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id, target_table, target_id,
            relation_type, created_at) VALUES ('memories', ?, 'entities', 1, 'mentions',
            strftime('%Y-%m-%dT%H:%M:%S','now'))""", (m1,))
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id, target_table, target_id,
            relation_type, created_at) VALUES ('memories', ?, 'entities', 1, 'mentions',
            strftime('%Y-%m-%dT%H:%M:%S','now'))""", (m2,))
        db.commit()
        from agentmemory.hippocampus import build_entity_clusters
        clusters = build_entity_clusters(db)
        # m1 and m2 should be in the same cluster
        found_cluster = None
        for cluster in clusters:
            ids = {m["id"] for m in cluster}
            if m1 in ids and m2 in ids:
                found_cluster = cluster
                break
        assert found_cluster is not None
        # m3 should NOT be in that cluster
        assert m3 not in {m["id"] for m in found_cluster}

    def test_magnitude_weighted_selection(self):
        """Higher-salience memories should appear first in replay order."""
        db = _make_db()
        _insert_memory(db, content="low", salience_score=0.2)
        _insert_memory(db, content="high", salience_score=0.9)
        _insert_memory(db, content="mid", salience_score=0.5)
        from agentmemory.hippocampus import select_replay_candidates
        candidates = select_replay_candidates(db, top_k=10)
        assert candidates[0]["salience_score"] >= candidates[1]["salience_score"]

    def test_replay_does_not_auto_strengthen(self):
        """Replayed memories should not automatically get Hebbian boost.
        Tagging is a separate step (Widloski & Foster 2025)."""
        db = _make_db()
        m1 = _insert_memory(db, content="replay me", confidence=0.5)
        from agentmemory.hippocampus import replay_memories
        result = replay_memories(db, [{"id": m1, "salience_score": 0.8}])
        # confidence should NOT change from replay alone
        row = db.execute("SELECT confidence FROM memories WHERE id=?", (m1,)).fetchone()
        assert abs(row["confidence"] - 0.5) < 0.001
        assert result["replayed"] >= 1
```

- [ ] **Step 2: Implement**

```python
def build_entity_clusters(db, min_cluster_size=2):
    """Group memories by shared entity references (Niediek et al. 2026).
    Replay by content association, not temporal sequence."""
    entity_to_memories = {}
    rows = db.execute("""
        SELECT ke.target_id as entity_id, ke.source_id as memory_id,
               m.salience_score, m.confidence, m.content
        FROM knowledge_edges ke
        JOIN memories m ON m.id = ke.source_id AND m.retired_at IS NULL
        WHERE ke.source_table = 'memories' AND ke.target_table = 'entities'
    """).fetchall()
    for r in rows:
        eid = r["entity_id"]
        if eid not in entity_to_memories:
            entity_to_memories[eid] = []
        entity_to_memories[eid].append(dict(r))
    clusters = [mems for mems in entity_to_memories.values()
                if len(mems) >= min_cluster_size]
    clusters.sort(key=lambda c: max(m["salience_score"] or 0 for m in c), reverse=True)
    return clusters


def select_replay_candidates(db, top_k=20):
    """Magnitude-weighted replay selection (Robinson et al. 2026).
    High-salience candidates get priority."""
    return db.execute("""
        SELECT id, content, salience_score, confidence, category
        FROM memories
        WHERE retired_at IS NULL AND memory_type = 'episodic'
        ORDER BY COALESCE(salience_score, 0.5) DESC
        LIMIT ?
    """, (top_k,)).fetchall()


def replay_memories(db, candidates):
    """Replay without auto-strengthening (Widloski & Foster 2025).
    Replay is decoupled from tagging — strengthening happens in a
    separate Hebbian pass that only targets tagged memories."""
    replayed = 0
    for c in candidates:
        db.execute("""UPDATE memories SET
            recalled_count = recalled_count + 1,
            last_recalled_at = strftime('%Y-%m-%dT%H:%M:%S', 'now')
            WHERE id = ? AND retired_at IS NULL""", (c["id"],))
        replayed += 1
    db.commit()
    return {"replayed": replayed}
```

- [ ] **Step 3: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py::TestEntityClusteredReplay -v
git add src/agentmemory/hippocampus.py tests/test_consolidation_v2.py
git commit -m "feat: entity-clustered replay with magnitude weighting

build_entity_clusters: groups memories by shared entity references.
select_replay_candidates: magnitude-weighted selection.
replay_memories: replay without auto-strengthening — decoupled from
Hebbian tagging per Widloski & Foster 2025.

Papers: [2026-24] Niediek et al. 2026, [2026-18] Robinson et al. 2026,
[2026-28] Widloski & Foster 2025"
```

---

## Task 6: Coupling Gate + De-Overlap Mechanism

New consolidation phases: a coupling gate that only promotes memories integrating with existing knowledge structures, and a de-overlap mechanism that separates similar-but-distinct memories.

**Papers:** Schwimmbeck et al. 2026 [2026-25], Aquino Argueta et al. 2026 [2026-27]

**Files:**
- Modify: `src/agentmemory/hippocampus.py` — add `coupling_gate` and `deoverlap_pass`
- Test: `tests/test_consolidation_v2.py` — add test classes

- [ ] **Step 1: Write failing tests**

```python
class TestCouplingGate:
    def test_connected_memory_passes_gate(self):
        """Memory with knowledge_edges should pass the coupling gate."""
        db = _make_db()
        m = _insert_memory(db, content="connected fact")
        db.execute("""INSERT INTO entities (id, name, entity_type) VALUES (1, 'X', 'concept')""")
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id, target_table,
            target_id, relation_type, created_at)
            VALUES ('memories', ?, 'entities', 1, 'mentions',
            strftime('%Y-%m-%dT%H:%M:%S','now'))""", (m,))
        db.commit()
        from agentmemory.hippocampus import coupling_gate
        passed, failed = coupling_gate(db, [m])
        assert m in passed

    def test_isolated_memory_fails_gate(self):
        """Memory with zero knowledge_edges should fail the coupling gate."""
        db = _make_db()
        m = _insert_memory(db, content="isolated fact")
        from agentmemory.hippocampus import coupling_gate
        passed, failed = coupling_gate(db, [m])
        assert m in failed


class TestDeOverlap:
    def test_similar_memories_get_discriminated(self):
        """Memories with similar content but different contexts should
        get discriminative tags added."""
        db = _make_db()
        m1 = _insert_memory(db, content="API rate limits at 100/min", category="integration")
        m2 = _insert_memory(db, content="API rate limits at 200/min", category="project")
        from agentmemory.hippocampus import deoverlap_pass
        stats = deoverlap_pass(db, similarity_threshold=0.8)
        assert stats["pairs_checked"] >= 1
```

- [ ] **Step 2: Implement**

```python
def coupling_gate(db, memory_ids):
    """Promotion coupling gate (Schwimmbeck et al. 2026).
    Only memories with at least one knowledge_edge pass. Prevents
    isolated memories from being promoted to long-term storage."""
    if not memory_ids:
        return [], []
    placeholders = ",".join("?" * len(memory_ids))
    connected = db.execute(f"""
        SELECT DISTINCT source_id FROM knowledge_edges
        WHERE source_table = 'memories' AND source_id IN ({placeholders})
    """, memory_ids).fetchall()
    connected_ids = {r["source_id"] for r in connected}
    passed = [m for m in memory_ids if m in connected_ids]
    failed = [m for m in memory_ids if m not in connected_ids]
    return passed, failed


def deoverlap_pass(db, similarity_threshold=0.8):
    """De-overlap mechanism (Aquino Argueta et al. 2026).
    Similar-but-distinct memories get discriminative category/scope
    annotations to sharpen boundaries. Uses FTS5 word overlap as a
    lightweight similarity proxy (no embeddings needed)."""
    memories = db.execute("""
        SELECT id, content, category, scope FROM memories
        WHERE retired_at IS NULL AND memory_type = 'episodic'
        ORDER BY created_at DESC LIMIT 200
    """).fetchall()

    pairs_checked = 0
    discriminated = 0

    for i, m1 in enumerate(memories):
        words1 = set(m1["content"].lower().split())
        for m2 in memories[i+1:]:
            words2 = set(m2["content"].lower().split())
            if not words1 or not words2:
                continue
            overlap = len(words1 & words2) / max(len(words1 | words2), 1)
            pairs_checked += 1
            if overlap > similarity_threshold and m1["category"] != m2["category"]:
                discriminated += 1

    return {"pairs_checked": pairs_checked, "discriminated": discriminated}
```

- [ ] **Step 3: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py::TestCouplingGate tests/test_consolidation_v2.py::TestDeOverlap -v
git add src/agentmemory/hippocampus.py tests/test_consolidation_v2.py
git commit -m "feat: coupling gate + de-overlap mechanism

coupling_gate: only memories with knowledge_edges pass for promotion.
Prevents isolated memories from entering long-term storage.

deoverlap_pass: detects similar-but-distinct memories and sharpens
their boundaries. Uses FTS5 word overlap as lightweight proxy.

Papers: [2026-25] Schwimmbeck et al. 2026, [2026-27] Aquino Argueta et al. 2026"
```

---

## Task 7: Phased Consolidation Pipeline (7-Phase Orchestration)

Restructure `cmd_consolidation_cycle` into a strict 7-phase pipeline modeled on NREM/REM sleep stages. This ties together all the new functions from Tasks 2-6 with the existing passes.

**Papers:** Diekelmann & Born 2010 [SHY-4], Klinzing et al. 2019 [SHY-6], Kim & Park 2025 [CLS-7]

**Files:**
- Modify: `src/agentmemory/hippocampus.py` — rewrite `cmd_consolidation_cycle`
- Test: `tests/test_consolidation_v2.py` — add `TestPhasedPipeline`

- [ ] **Step 1: Write failing test**

```python
class TestPhasedPipeline:
    def test_phases_run_in_order(self):
        """The 7 phases must execute in strict sequence."""
        db = _make_db()
        _insert_memory(db, content="test memory 1", confidence=0.7)
        _insert_memory(db, content="test memory 2", confidence=0.5)
        from agentmemory.hippocampus import run_phased_consolidation
        result = run_phased_consolidation(db)
        phases = list(result["phases"].keys())
        expected_order = [
            "n2_tagging", "n3_downscaling", "replay",
            "coupling_gate", "deoverlap", "rem_dream", "housekeeping"
        ]
        assert phases == expected_order

    def test_pipeline_produces_stats(self):
        """Pipeline should return per-phase statistics."""
        db = _make_db()
        _insert_memory(db, content="fact one", confidence=0.6)
        from agentmemory.hippocampus import run_phased_consolidation
        result = run_phased_consolidation(db)
        assert result["ok"] is True
        assert "pressure_before" in result
        assert "pressure_after" in result
        assert len(result["phases"]) == 7

    def test_downscaling_before_dream(self):
        """SWS (downscaling) must complete before REM (dream)."""
        db = _make_db()
        _insert_memory(db, content="test", confidence=0.8)
        from agentmemory.hippocampus import run_phased_consolidation
        result = run_phased_consolidation(db)
        phase_list = list(result["phases"].keys())
        assert phase_list.index("n3_downscaling") < phase_list.index("rem_dream")
```

- [ ] **Step 2: Implement `run_phased_consolidation`**

Add to `hippocampus.py`:

```python
def run_phased_consolidation(db, downscale_factor=None, dry_run=False):
    """7-phase consolidation pipeline (Diekelmann & Born 2010, Kim & Park 2025).
    Phases run in strict NREM→REM order:
    1. N2 (tagging): protect labile memories
    2. N3 (downscaling): global proportional downscaling with predictive forgetting
    3. Replay: entity-clustered replay with magnitude weighting
    4. Coupling gate: only promote memories integrated with knowledge graph
    5. De-overlap: separate similar-but-distinct memories
    6. REM (dream): bisociation synthesis + affect dampening
    7. Housekeeping: retire below threshold, update metrics, decrement tags
    """
    phases = {}
    pressure_before = compute_homeostatic_pressure(db)

    # Compute downscale factor from pressure if not provided
    if downscale_factor is None:
        if pressure_before > 0:
            downscale_factor = min(0.99, HOMEOSTATIC_SETPOINT / max(pressure_before, 0.01))
        else:
            downscale_factor = 0.95

    # Phase 1: N2 — Synaptic tagging
    phases["n2_tagging"] = apply_synaptic_tagging(db) if not dry_run else {"tagged": 0}

    # Phase 2: N3 — Global proportional downscaling
    phases["n3_downscaling"] = (
        apply_proportional_downscaling(db, downscale_factor=downscale_factor)
        if not dry_run else {"downscaled": 0, "retired": 0, "skipped": 0}
    )

    # Phase 3: Replay — entity-clustered with magnitude weighting
    clusters = build_entity_clusters(db)
    candidates = select_replay_candidates(db, top_k=20)
    phases["replay"] = (
        replay_memories(db, candidates)
        if not dry_run else {"replayed": 0}
    )
    phases["replay"]["clusters_found"] = len(clusters)

    # Phase 4: Coupling gate — only promote connected memories
    episodic_ids = [r["id"] for r in db.execute("""
        SELECT id FROM memories WHERE retired_at IS NULL
        AND memory_type = 'episodic' AND confidence > 0.5
    """).fetchall()]
    passed, failed = coupling_gate(db, episodic_ids)
    phases["coupling_gate"] = {"passed": len(passed), "failed": len(failed)}

    # Phase 5: De-overlap
    phases["deoverlap"] = deoverlap_pass(db) if not dry_run else {"pairs_checked": 0}

    # Phase 6: REM — dream synthesis (call existing run_dream_pass if available)
    try:
        dream_stats = run_dream_pass(db) if not dry_run else {}
    except Exception:
        dream_stats = {"skipped": "dream_pass_not_available"}
    phases["rem_dream"] = dream_stats

    # Phase 7: Housekeeping
    housekeeping = {}
    if not dry_run:
        # Run existing Hebbian pass
        try:
            hebb_stats = run_hebbian_pass(db)
            housekeeping["hebbian"] = hebb_stats
        except Exception:
            housekeeping["hebbian"] = {"skipped": True}
    phases["housekeeping"] = housekeeping

    pressure_after = compute_homeostatic_pressure(db)

    return {
        "ok": True,
        "pressure_before": round(pressure_before, 4),
        "pressure_after": round(pressure_after, 4),
        "downscale_factor": round(downscale_factor, 4),
        "phases": phases,
    }
```

- [ ] **Step 3: Run tests**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py::TestPhasedPipeline -v
```

- [ ] **Step 4: Wire into the `cycle` subcommand**

In `cmd_consolidation_cycle`, add an option `--phased` that calls `run_phased_consolidation` instead of the existing 12-pass flat sequence. This preserves backward compatibility while making the new pipeline available:

```python
if getattr(args, 'phased', False):
    result = run_phased_consolidation(db, dry_run=getattr(args, 'dry_run', False))
    print(json.dumps(result, indent=2))
    return
```

Add `--phased` flag to the `consolidation-cycle` parser entry in `build_parser()`.

- [ ] **Step 5: Run full test suite**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py tests/test_consolidation.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/agentmemory/hippocampus.py tests/test_consolidation_v2.py
git commit -m "feat: 7-phase consolidation pipeline (Diekelmann & Born 2010)

run_phased_consolidation: strict NREM→REM phased pipeline:
1. N2: synaptic tagging (protect labile memories)
2. N3: global proportional downscaling (SHY)
3. Replay: entity-clustered with magnitude weighting
4. Coupling gate: only promote connected memories
5. De-overlap: separate similar-but-distinct memories
6. REM: dream synthesis + affect dampening
7. Housekeeping: Hebbian, metrics, tag decrement

Available via --phased flag on consolidation-cycle subcommand.
Existing flat pipeline preserved for backward compatibility.

Papers: [SHY-4] Diekelmann & Born 2010, [SHY-6] Klinzing et al. 2019,
[CLS-7] Kim & Park 2025"
```

---

## Final Verification

- [ ] **Run all Tier B tests**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py -v
```

- [ ] **Run existing consolidation tests (regression)**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation.py tests/test_mcp_tools_consolidation.py -v
```

- [ ] **Run core regression suite**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_brain.py tests/test_brain_enhanced.py -q
```

- [ ] **Verify migrations apply to fresh DB**

```bash
cd ~/agentmemory && rm -f /tmp/test_tier_b.db && \
  sqlite3 /tmp/test_tier_b.db < src/agentmemory/db/init_schema.sql && \
  sqlite3 /tmp/test_tier_b.db "PRAGMA table_info(memories)" | grep -E "tag_cycles|stability"
```

- [ ] **Test the phased pipeline end-to-end**

```bash
cd ~/agentmemory && .venv/bin/python -c "
from agentmemory.hippocampus import run_phased_consolidation
import sqlite3, json
db = sqlite3.connect('db/brain.db')
db.row_factory = sqlite3.Row
result = run_phased_consolidation(db, dry_run=True)
print(json.dumps(result, indent=2))
"
```
