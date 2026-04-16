# Tier A: Neuroscience-Grounded Quick Wins — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 7 retrieval and write-gate improvements backed by peer-reviewed neuroscience and ML research, measurable on the existing bench harness.

**Architecture:** All changes modify existing functions in `_impl.py` and `mcp_server.py` / `mcp_tools_consolidation.py`. One new migration (037) adds `encoding_affect_id` to the memories table. No new files. Each task is independently testable and committable.

**Tech Stack:** Python 3.11+, SQLite, pytest. No new dependencies.

**Spec:** `research/wave14/32_neuroscience_grounded_improvements.md` (Sections 6, 12)

---

## Task 1: Retrieval-Practice Strengthening

The single highest-ROI change. Successful recall should boost confidence via desirable-difficulty weighting. Currently `recalled_count` increments and `alpha += 1` but the boost is flat — hard retrievals (high prediction error) should strengthen more than easy ones.

**Papers:** Roediger & Karpicke 2006 [CE-5], Bjork 1994 [CE-9], Kehl et al. 2026 [2026-21]

**Files:**
- Modify: `src/agentmemory/_impl.py:4844-4863` (recall update SQL in cmd_search)
- Modify: `src/agentmemory/_impl.py:1937-1942` (recall update SQL in simpler search path)
- Test: `tests/test_retrieval_practice.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_retrieval_practice.py`:

```python
"""Tests for retrieval-practice strengthening (Roediger & Karpicke 2006)."""
import sqlite3
import pytest
from agentmemory._impl import DB_PATH, _days_since

def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    from agentmemory._impl import _init_schema
    _init_schema(db)
    return db

def _insert_memory(db, content, confidence=0.5, alpha=2.0, beta=2.0,
                   retrieval_prediction_error=None):
    db.execute(
        """INSERT INTO memories (agent_id, content, category, confidence,
           alpha, beta, retrieval_prediction_error, scope,
           created_at, updated_at)
           VALUES (?, ?, 'lesson', ?, ?, ?, ?, 'global',
                   strftime('%Y-%m-%dT%H:%M:%S','now'),
                   strftime('%Y-%m-%dT%H:%M:%S','now'))""",
        ("test", content, confidence, alpha, beta,
         retrieval_prediction_error, ))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestRetrievalPracticeStrengthening:
    def test_successful_recall_boosts_confidence(self):
        """A recalled memory's confidence should increase."""
        db = _make_db()
        mid = _insert_memory(db, "test fact", confidence=0.5, alpha=2.0, beta=2.0)
        from agentmemory._impl import _retrieval_practice_boost
        _retrieval_practice_boost(db, mid, retrieval_prediction_error=0.0)
        row = db.execute("SELECT confidence, alpha FROM memories WHERE id=?",
                         (mid,)).fetchone()
        assert row["confidence"] > 0.5
        assert row["alpha"] > 2.0

    def test_hard_retrieval_boosts_more(self):
        """High prediction error (hard retrieval) should produce a larger
        confidence boost than low prediction error (easy retrieval)."""
        db = _make_db()
        easy_id = _insert_memory(db, "easy fact", confidence=0.5)
        hard_id = _insert_memory(db, "hard fact", confidence=0.5)
        from agentmemory._impl import _retrieval_practice_boost
        _retrieval_practice_boost(db, easy_id, retrieval_prediction_error=0.1)
        _retrieval_practice_boost(db, hard_id, retrieval_prediction_error=0.8)
        easy = db.execute("SELECT confidence FROM memories WHERE id=?",
                          (easy_id,)).fetchone()
        hard = db.execute("SELECT confidence FROM memories WHERE id=?",
                          (hard_id,)).fetchone()
        assert hard["confidence"] > easy["confidence"]

    def test_confidence_capped_at_one(self):
        """Confidence should never exceed 1.0 regardless of boost count."""
        db = _make_db()
        mid = _insert_memory(db, "strong fact", confidence=0.98, alpha=50.0, beta=1.0)
        from agentmemory._impl import _retrieval_practice_boost
        for _ in range(10):
            _retrieval_practice_boost(db, mid, retrieval_prediction_error=0.9)
        row = db.execute("SELECT confidence FROM memories WHERE id=?",
                         (mid,)).fetchone()
        assert row["confidence"] <= 1.0

    def test_labile_window_reset_on_recall(self):
        """Successful recall should extend the labile window."""
        db = _make_db()
        mid = _insert_memory(db, "labile fact", confidence=0.5)
        from agentmemory._impl import _retrieval_practice_boost
        _retrieval_practice_boost(db, mid, retrieval_prediction_error=0.5)
        row = db.execute("SELECT labile_until FROM memories WHERE id=?",
                         (mid,)).fetchone()
        assert row["labile_until"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_retrieval_practice.py -v`
Expected: FAIL with `ImportError: cannot import name '_retrieval_practice_boost'`

- [ ] **Step 3: Implement `_retrieval_practice_boost` function**

Add to `src/agentmemory/_impl.py` near the recall-update code (after line ~4860):

```python
_RETRIEVAL_PRACTICE_BASE_BOOST = 0.02

def _retrieval_practice_boost(db, memory_id, retrieval_prediction_error=0.0):
    """Retrieval-practice strengthening (Roediger & Karpicke 2006).
    Hard retrievals (high RPE) boost more (desirable difficulties, Bjork 1994)."""
    rpe = max(0.0, min(1.0, retrieval_prediction_error or 0.0))
    boost = _RETRIEVAL_PRACTICE_BASE_BOOST * (1.0 + rpe)
    db.execute("""
        UPDATE memories SET
            confidence = MIN(1.0, confidence + ?),
            alpha = COALESCE(alpha, 1.0) + 1.0,
            recalled_count = recalled_count + 1,
            last_recalled_at = strftime('%Y-%m-%dT%H:%M:%S', 'now'),
            labile_until = strftime('%Y-%m-%dT%H:%M:%S',
                                    'now', '+2 hours')
        WHERE id = ? AND retired_at IS NULL
    """, (boost, memory_id))
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_retrieval_practice.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Wire into cmd_search recall-update path**

Replace the recall-update SQL at `_impl.py:4847-4858` to call `_retrieval_practice_boost` instead of inline SQL. The existing `UPDATE memories SET recalled_count...` block is replaced by a call to the new function, passing `retrieval_prediction_error` from the memory row if available.

- [ ] **Step 6: Run the full search quality bench to verify no regression**

Run: `cd ~/agentmemory && .venv/bin/python -m tests.bench.run --check`
Expected: PASS (no >2% regression on headline metrics)

- [ ] **Step 7: Commit**

```bash
git add tests/test_retrieval_practice.py src/agentmemory/_impl.py
git commit -m "feat: retrieval-practice strengthening (Roediger & Karpicke 2006)

Successful recall now boosts confidence by BASE_BOOST * (1 + RPE).
Hard retrievals (high prediction error) boost more than easy ones
(desirable difficulties, Bjork 1994). Labile window is reset on
each recall. Confidence capped at 1.0.

Papers: [CE-5] Roediger & Karpicke 2006, [CE-9] Bjork 1994,
[2026-21] Kehl et al. 2026"
```

---

## Task 2: Thompson Sampling Retrieval

Replace the confidence point-estimate in search reranking with a Thompson sample drawn from Beta(alpha, beta). Converts static retrieval into an explore/exploit learner with zero new columns.

**Papers:** Thompson 1933 [ML-1], Glowacka 2019 [ML-2]

**Files:**
- Modify: `src/agentmemory/_impl.py:4531-4613` (in `_apply_recency_and_trim`, confidence usage in scoring)
- Test: `tests/test_thompson_sampling.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_thompson_sampling.py`:

```python
"""Tests for Thompson Sampling retrieval (Thompson 1933)."""
import random
import pytest


class TestThompsonSampling:
    def test_thompson_sample_within_bounds(self):
        """Thompson sample from Beta(alpha, beta) must be in [0, 1]."""
        from agentmemory._impl import _thompson_confidence
        for _ in range(1000):
            sample = _thompson_confidence(alpha=1.0, beta=1.0)
            assert 0.0 <= sample <= 1.0

    def test_high_alpha_biases_high(self):
        """High alpha (many successes) should produce samples near 1.0."""
        from agentmemory._impl import _thompson_confidence
        random.seed(42)
        samples = [_thompson_confidence(alpha=50.0, beta=1.0) for _ in range(100)]
        mean = sum(samples) / len(samples)
        assert mean > 0.9

    def test_high_beta_biases_low(self):
        """High beta (many failures) should produce samples near 0.0."""
        from agentmemory._impl import _thompson_confidence
        random.seed(42)
        samples = [_thompson_confidence(alpha=1.0, beta=50.0) for _ in range(100)]
        mean = sum(samples) / len(samples)
        assert mean < 0.1

    def test_uncertain_memory_explores(self):
        """Low alpha+beta (uncertain) should produce high variance."""
        from agentmemory._impl import _thompson_confidence
        random.seed(42)
        samples = [_thompson_confidence(alpha=1.0, beta=1.0) for _ in range(200)]
        variance = sum((s - 0.5) ** 2 for s in samples) / len(samples)
        assert variance > 0.05  # high variance = exploration

    def test_certain_memory_exploits(self):
        """High alpha+beta (certain) should produce low variance."""
        from agentmemory._impl import _thompson_confidence
        random.seed(42)
        samples = [_thompson_confidence(alpha=50.0, beta=50.0) for _ in range(200)]
        mean = sum(samples) / len(samples)
        variance = sum((s - mean) ** 2 for s in samples) / len(samples)
        assert variance < 0.005  # low variance = exploitation
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_thompson_sampling.py -v`
Expected: FAIL with `ImportError: cannot import name '_thompson_confidence'`

- [ ] **Step 3: Implement `_thompson_confidence`**

Add to `src/agentmemory/_impl.py` near the search scoring code:

```python
import random as _random

def _thompson_confidence(alpha=1.0, beta=1.0):
    """Thompson sample from Beta(alpha, beta) for explore/exploit retrieval.
    Thompson 1933; Glowacka 2019 shows this outperforms UCB in non-stationary IR."""
    a = max(0.01, alpha or 1.0)
    b = max(0.01, beta or 1.0)
    return _random.betavariate(a, b)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_thompson_sampling.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Wire into `_apply_recency_and_trim`**

In the scoring formula at `_impl.py:4531-4613`, where `confidence` is used as part of the salience computation, replace:
```python
confidence = row["confidence"] or 0.5
```
with:
```python
confidence = _thompson_confidence(
    alpha=row["alpha"] or 1.0,
    beta=row["beta"] or 1.0,
)
```

This applies only in the adaptive-salience branch of `_apply_recency_and_trim`.

- [ ] **Step 6: Run bench harness**

Run: `cd ~/agentmemory && .venv/bin/python -m tests.bench.run --check`
Expected: PASS (Thompson Sampling introduces controlled variance; average metrics should hold)

- [ ] **Step 7: Commit**

```bash
git add tests/test_thompson_sampling.py src/agentmemory/_impl.py
git commit -m "feat: Thompson Sampling retrieval (explore/exploit)

Replace confidence point-estimate in search reranking with a Thompson
sample from Beta(alpha, beta). Memories with uncertain confidence
(low alpha+beta) get explored more; certain memories get exploited.
Zero new columns — uses existing alpha/beta.

Papers: [ML-1] Thompson 1933, [ML-2] Glowacka 2019"
```

---

## Task 3: Temporal Contiguity Bonus

When a memory is retrieved, boost retrieval scores of temporally adjacent memories from the same session. The brain recalls related events in sequence.

**Papers:** Dong et al. 2026 [2026-30] (Trends in Cognitive Sciences)

**Files:**
- Modify: `src/agentmemory/_impl.py` (in `_apply_recency_and_trim` or post-scoring step)
- Test: `tests/test_temporal_contiguity.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_temporal_contiguity.py`:

```python
"""Tests for temporal contiguity bonus (Dong et al. 2026)."""
import pytest
from datetime import datetime, timedelta


class TestTemporalContiguity:
    def test_adjacent_memories_get_bonus(self):
        """Memories created within 30 min of a retrieved memory
        by the same agent should get a score boost."""
        from agentmemory._impl import _apply_temporal_contiguity
        now = datetime.fromisoformat("2026-04-15T12:00:00")
        retrieved_at = now
        candidates = [
            {"id": 1, "score": 1.0, "created_at": "2026-04-15T11:45:00",
             "agent_id": "a1"},  # 15 min before — should boost
            {"id": 2, "score": 1.0, "created_at": "2026-04-15T10:00:00",
             "agent_id": "a1"},  # 2 hrs before — no boost
            {"id": 3, "score": 1.0, "created_at": "2026-04-15T11:50:00",
             "agent_id": "a2"},  # 10 min before, different agent — no boost
        ]
        boosted = _apply_temporal_contiguity(
            candidates, retrieved_at=retrieved_at, agent_id="a1")
        assert boosted[0]["score"] > 1.0  # adjacent, same agent
        assert boosted[1]["score"] == 1.0  # too far
        assert boosted[2]["score"] == 1.0  # different agent

    def test_contiguity_window_is_30_minutes(self):
        """Memories exactly at the 30-min boundary should NOT get boosted."""
        from agentmemory._impl import _apply_temporal_contiguity
        now = datetime.fromisoformat("2026-04-15T12:00:00")
        candidates = [
            {"id": 1, "score": 1.0, "created_at": "2026-04-15T11:30:00",
             "agent_id": "a1"},  # exactly 30 min — boundary, no boost
            {"id": 2, "score": 1.0, "created_at": "2026-04-15T11:31:00",
             "agent_id": "a1"},  # 29 min — should boost
        ]
        boosted = _apply_temporal_contiguity(
            candidates, retrieved_at=now, agent_id="a1")
        assert boosted[0]["score"] == 1.0
        assert boosted[1]["score"] > 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_temporal_contiguity.py -v`
Expected: FAIL with `ImportError: cannot import name '_apply_temporal_contiguity'`

- [ ] **Step 3: Implement `_apply_temporal_contiguity`**

```python
_CONTIGUITY_WINDOW_MINUTES = 30
_CONTIGUITY_BONUS = 1.15

def _apply_temporal_contiguity(candidates, retrieved_at, agent_id):
    """Temporal contiguity bonus (Dong et al. 2026, Trends in Cognitive Sciences).
    Boost scores of memories created within 30 min of a retrieved memory by
    the same agent."""
    if not retrieved_at or not agent_id:
        return candidates
    for c in candidates:
        try:
            c_time = datetime.fromisoformat(c["created_at"])
        except (ValueError, TypeError):
            continue
        delta = abs((retrieved_at - c_time).total_seconds())
        if delta < _CONTIGUITY_WINDOW_MINUTES * 60 and c.get("agent_id") == agent_id:
            c["score"] = c["score"] * _CONTIGUITY_BONUS
    return candidates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_temporal_contiguity.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Wire into cmd_search post-scoring**

After the main scoring loop in `cmd_search`, apply temporal contiguity to the top result's temporal neighbors. Only applies when at least one result was found.

- [ ] **Step 6: Run bench harness**

Run: `cd ~/agentmemory && .venv/bin/python -m tests.bench.run --check`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_temporal_contiguity.py src/agentmemory/_impl.py
git commit -m "feat: temporal contiguity bonus for search (Dong et al. 2026)

Memories created within 30 min of a retrieved memory by the same agent
get a 15% score boost. Mimics the brain's tendency to recall related
events in sequence.

Papers: [2026-30] Dong et al. 2026, Trends in Cognitive Sciences"
```

---

## Task 4: Modification Resistance for Reconsolidation

Memories should develop resistance to reconsolidation that increases with age, recall count, and EWC importance. The W(m) surprise signal must exceed this resistance to open a labile window.

**Papers:** O'Neill & Winters 2026 [2026-22] (Neuroscience)

**Files:**
- Modify: `src/agentmemory/mcp_tools_consolidation.py:42-63` (`_is_labile`)
- Modify: `src/agentmemory/mcp_server.py:1015-1038` (labile window opening)
- Test: `tests/test_modification_resistance.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_modification_resistance.py`:

```python
"""Tests for modification resistance (O'Neill & Winters 2026)."""
import math
import pytest


class TestModificationResistance:
    def test_young_memory_low_resistance(self):
        """A new memory with few recalls should have low resistance."""
        from agentmemory._impl import _modification_resistance
        r = _modification_resistance(days_old=1, recalled_count=0,
                                      ewc_importance=0.0)
        assert r < 0.2

    def test_old_frequent_memory_high_resistance(self):
        """An old, frequently recalled memory should resist modification."""
        from agentmemory._impl import _modification_resistance
        r = _modification_resistance(days_old=90, recalled_count=20,
                                      ewc_importance=0.8)
        assert r > 0.6

    def test_resistance_capped_below_one(self):
        """Resistance should never reach 1.0 — everything is modifiable
        with enough surprise."""
        from agentmemory._impl import _modification_resistance
        r = _modification_resistance(days_old=3650, recalled_count=1000,
                                      ewc_importance=1.0)
        assert r <= 0.9

    def test_surprise_must_exceed_resistance(self):
        """Labile window should only open when surprise > resistance."""
        from agentmemory._impl import _should_open_labile_window
        assert _should_open_labile_window(surprise=0.9, resistance=0.3) is True
        assert _should_open_labile_window(surprise=0.2, resistance=0.5) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_modification_resistance.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement resistance functions**

Add to `src/agentmemory/_impl.py`:

```python
def _modification_resistance(days_old, recalled_count, ewc_importance):
    """Modification resistance increases with age, recall, and importance.
    O'Neill & Winters 2026: dopamine/surprise must breach boundary
    conditions to enable memory modification."""
    age_term = 0.1 * math.log(1.0 + max(0, days_old))
    recall_term = 0.05 * max(0, recalled_count)
    ewc_term = 0.3 * max(0.0, min(1.0, ewc_importance or 0.0))
    return min(0.9, age_term + recall_term + ewc_term)


def _should_open_labile_window(surprise, resistance):
    """Labile window opens only when surprise exceeds resistance."""
    return surprise > resistance
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_modification_resistance.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Wire into labile-window-opening code in `mcp_server.py`**

In `tool_event_add` at `mcp_server.py:1015-1038`, before setting `labile_until`, compute resistance for each candidate memory and only open the labile window if the event's importance (as the surprise analog) exceeds the memory's resistance.

- [ ] **Step 6: Run existing reconsolidation tests**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_mcp_tools_consolidation.py -v`
Expected: All existing tests PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_modification_resistance.py src/agentmemory/_impl.py src/agentmemory/mcp_server.py
git commit -m "feat: modification resistance for reconsolidation (O'Neill & Winters 2026)

Memories develop resistance to reconsolidation based on age, recall
count, and EWC importance. The surprise signal must exceed resistance
to open a labile window. Prevents trivial destabilization of strong
memories while allowing high-surprise events to update them.

Papers: [2026-22] O'Neill & Winters 2026, Neuroscience"
```

---

## Task 5: Encoding Affect Linkage (Migration 037)

Link each memory to the agent's affect state at encoding time. Use affect-distance as a retrieval reranking signal, weighted higher for internally-generated categories.

**Papers:** Eich & Metcalfe 1989 [CE-10], Morici et al. 2026 [2026-20]

**Files:**
- Create: `db/migrations/037_encoding_affect_linkage.sql`
- Modify: `src/agentmemory/db/init_schema.sql` (add column to memories CREATE TABLE)
- Modify: `src/agentmemory/mcp_server.py` (tool_memory_add: populate encoding_affect_id)
- Modify: `src/agentmemory/_impl.py` (cmd_mem_add: populate encoding_affect_id)
- Test: `tests/test_encoding_affect.py` (new)

- [ ] **Step 1: Write the migration**

Create `db/migrations/037_encoding_affect_linkage.sql`:

```sql
-- Migration 037: encoding affect linkage (Eich & Metcalfe 1989)
-- Link memories to the agent's affect state at encoding time.
ALTER TABLE memories ADD COLUMN encoding_affect_id INTEGER
    REFERENCES affect_log(id) DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_memories_encoding_affect
    ON memories(encoding_affect_id) WHERE encoding_affect_id IS NOT NULL;
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_encoding_affect.py`:

```python
"""Tests for encoding affect linkage (Eich & Metcalfe 1989)."""
import sqlite3
import pytest


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    from agentmemory._impl import _init_schema
    _init_schema(db)
    return db


class TestEncodingAffectLinkage:
    def test_memory_add_captures_encoding_affect(self):
        """memory_add should populate encoding_affect_id from most recent
        affect_log entry for the same agent."""
        db = _make_db()
        db.execute("""INSERT INTO affect_log
            (agent_id, valence, arousal, dominance, affect_label,
             created_at)
            VALUES ('a1', 0.5, 0.7, 0.3, 'focused',
                    strftime('%Y-%m-%dT%H:%M:%S','now'))""")
        affect_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()
        from agentmemory._impl import _get_encoding_affect_id
        result = _get_encoding_affect_id(db, "a1")
        assert result == affect_id

    def test_no_affect_log_returns_none(self):
        """If no affect_log entry exists for the agent, return None."""
        db = _make_db()
        from agentmemory._impl import _get_encoding_affect_id
        result = _get_encoding_affect_id(db, "nonexistent")
        assert result is None

    def test_affect_distance_computation(self):
        """Affect distance should be Euclidean in VAD space."""
        from agentmemory._impl import _affect_distance
        d = _affect_distance(
            v1=0.5, a1=0.7, d1=0.3,
            v2=-0.5, a2=0.1, d2=0.8,
        )
        assert d > 0
        assert _affect_distance(0.5, 0.7, 0.3, 0.5, 0.7, 0.3) == 0.0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_encoding_affect.py -v`
Expected: FAIL

- [ ] **Step 4: Implement helpers**

Add to `src/agentmemory/_impl.py`:

```python
def _get_encoding_affect_id(db, agent_id):
    """Get the most recent affect_log ID for this agent."""
    row = db.execute(
        """SELECT id FROM affect_log
           WHERE agent_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (agent_id,)).fetchone()
    return row["id"] if row else None


def _affect_distance(v1, a1, d1, v2, a2, d2):
    """Euclidean distance in VAD (valence-arousal-dominance) space."""
    return math.sqrt((v1 - v2) ** 2 + (a1 - a2) ** 2 + (d1 - d2) ** 2)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_encoding_affect.py -v`
Expected: 3 PASSED

- [ ] **Step 6: Wire `_get_encoding_affect_id` into memory_add paths**

In `cmd_mem_add` (the CLI memory add handler) and `tool_memory_add` (the MCP handler), after the memory INSERT, set `encoding_affect_id`:

```python
encoding_affect_id = _get_encoding_affect_id(db, agent_id)
if encoding_affect_id:
    db.execute("UPDATE memories SET encoding_affect_id = ? WHERE id = ?",
               (encoding_affect_id, memory_id))
```

- [ ] **Step 7: Add init_schema.sql column**

Add `encoding_affect_id INTEGER REFERENCES affect_log(id) DEFAULT NULL` to the memories CREATE TABLE in `init_schema.sql`, after the existing `retrieval_prediction_error` column.

- [ ] **Step 8: Run migration on test DB and verify**

```bash
cd ~/agentmemory && sqlite3 db/brain.db < db/migrations/037_encoding_affect_linkage.sql
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_encoding_affect.py tests/test_mcp_integration.py -v
```

- [ ] **Step 9: Commit**

```bash
git add db/migrations/037_encoding_affect_linkage.sql \
        src/agentmemory/db/init_schema.sql \
        src/agentmemory/_impl.py \
        src/agentmemory/mcp_server.py \
        tests/test_encoding_affect.py
git commit -m "feat: encoding affect linkage (migration 037)

Link memories to the agent's affect state at encoding time via
encoding_affect_id FK to affect_log. Memory_add auto-populates
from the most recent affect_log entry. Adds _affect_distance()
for VAD-space Euclidean distance, to be wired into reranking in
a follow-up task.

Papers: [CE-10] Eich & Metcalfe 1989, [2026-20] Morici et al. 2026"
```

---

## Task 6: A-MAC 5-Factor Write Gate

Replace the W(m) surprise-only scoring with A-MAC's 5-factor decomposition: future utility, factual confidence, semantic novelty, temporal recency, and content type prior.

**Papers:** Zhang et al. 2026 [2026-5] (A-MAC, ICLR 2026 Workshop)

**Files:**
- Modify: `src/agentmemory/_impl.py:1467-1527` (pre-worthiness calculation)
- Test: `tests/test_amac_gate.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_amac_gate.py`:

```python
"""Tests for A-MAC 5-factor write gate (Zhang et al. 2026)."""
import pytest


class TestAMACGate:
    def test_five_factors_contribute(self):
        """All five factors should influence the final score."""
        from agentmemory._impl import _amac_worthiness
        base = _amac_worthiness(
            future_utility=0.5, factual_confidence=0.5,
            semantic_novelty=0.5, temporal_recency=0.5,
            content_type_prior=0.5)
        high_utility = _amac_worthiness(
            future_utility=0.9, factual_confidence=0.5,
            semantic_novelty=0.5, temporal_recency=0.5,
            content_type_prior=0.5)
        assert high_utility > base

    def test_content_type_prior_most_influential(self):
        """Per A-MAC, content type prior is the single most influential
        factor. A high prior with low other factors should still pass."""
        from agentmemory._impl import _amac_worthiness
        high_prior = _amac_worthiness(
            future_utility=0.3, factual_confidence=0.3,
            semantic_novelty=0.3, temporal_recency=0.3,
            content_type_prior=0.95)
        low_prior = _amac_worthiness(
            future_utility=0.7, factual_confidence=0.7,
            semantic_novelty=0.7, temporal_recency=0.7,
            content_type_prior=0.1)
        assert high_prior > low_prior

    def test_score_bounded_zero_one(self):
        """Score should always be in [0, 1]."""
        from agentmemory._impl import _amac_worthiness
        for vals in [(0, 0, 0, 0, 0), (1, 1, 1, 1, 1), (0.5, 0.5, 0.5, 0.5, 0.5)]:
            score = _amac_worthiness(*vals)
            assert 0.0 <= score <= 1.0

    def test_all_zero_rejected(self):
        """All-zero factors should produce a score below the gate threshold."""
        from agentmemory._impl import _amac_worthiness
        score = _amac_worthiness(0, 0, 0, 0, 0)
        assert score < 0.3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_amac_gate.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `_amac_worthiness`**

```python
_AMAC_WEIGHTS = {
    "future_utility": 0.15,
    "factual_confidence": 0.15,
    "semantic_novelty": 0.20,
    "temporal_recency": 0.10,
    "content_type_prior": 0.40,  # most influential per A-MAC
}

def _amac_worthiness(future_utility, factual_confidence, semantic_novelty,
                      temporal_recency, content_type_prior):
    """A-MAC 5-factor write gate (Zhang et al. 2026, ICLR Workshop).
    Content type prior is the single most influential factor."""
    w = _AMAC_WEIGHTS
    score = (
        w["future_utility"] * max(0.0, min(1.0, future_utility)) +
        w["factual_confidence"] * max(0.0, min(1.0, factual_confidence)) +
        w["semantic_novelty"] * max(0.0, min(1.0, semantic_novelty)) +
        w["temporal_recency"] * max(0.0, min(1.0, temporal_recency)) +
        w["content_type_prior"] * max(0.0, min(1.0, content_type_prior))
    )
    return max(0.0, min(1.0, score))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_amac_gate.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Wire into pre-worthiness gate**

Replace the `_pre_worthiness` formula at `_impl.py:1467-1527` to call `_amac_worthiness`, mapping existing signals to the five factors:
- `future_utility` = demand_forecast score if available, else 0.5
- `factual_confidence` = source trust weight (existing `source_weight_applied`)
- `semantic_novelty` = existing surprise score
- `temporal_recency` = `1.0 - min(1.0, days_since_last_similar / 30.0)`
- `content_type_prior` = historical accept rate for this category (query `schema_versions` or maintain a simple counter)

Keep the existing 0.3 rejection threshold and 0.7 full-evolution threshold.

- [ ] **Step 6: Run the full test suite**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/ -q --timeout=60`
Expected: All tests pass, no regressions

- [ ] **Step 7: Commit**

```bash
git add tests/test_amac_gate.py src/agentmemory/_impl.py
git commit -m "feat: A-MAC 5-factor write gate (Zhang et al. 2026)

Replace single-score W(m) gate with A-MAC's 5-factor decomposition:
future utility (0.15), factual confidence (0.15), semantic novelty
(0.20), temporal recency (0.10), content type prior (0.40).
Content type prior is the most influential factor per A-MAC paper.
Same rejection/evolution thresholds preserved.

Papers: [2026-5] Zhang et al. 2026, ICLR Workshop MemAgents"
```

---

## Task 7: W(m) Gate Calibration Feedback Loop

Track whether the A-MAC gate is well-calibrated: do high-scoring memories actually get recalled more than low-scoring ones?

**Papers:** Dunlosky & Metcalfe 2009 [ML-7], Nelson & Narens 1990 [ML-6]

**Files:**
- Modify: `src/agentmemory/_impl.py` (add calibration check to lint/health)
- Test: `tests/test_gate_calibration.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_gate_calibration.py`:

```python
"""Tests for W(m) gate calibration (Nelson & Narens 1990)."""
import sqlite3
import pytest


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    from agentmemory._impl import _init_schema
    _init_schema(db)
    return db


class TestGateCalibration:
    def test_calibration_with_no_memories_returns_none(self):
        """Empty brain should return None (insufficient data)."""
        db = _make_db()
        from agentmemory._impl import _gate_calibration_score
        assert _gate_calibration_score(db) is None

    def test_well_calibrated_gate(self):
        """If high-W(m) memories are recalled more, calibration > 0."""
        db = _make_db()
        # High W(m), high recall
        db.execute("""INSERT INTO memories (agent_id, content, category,
            confidence, recalled_count, scope, created_at, updated_at)
            VALUES ('a1', 'important', 'lesson', 0.9, 10, 'global',
            '2026-01-01T00:00:00', '2026-01-01T00:00:00')""")
        # Low W(m), low recall
        db.execute("""INSERT INTO memories (agent_id, content, category,
            confidence, recalled_count, scope, created_at, updated_at)
            VALUES ('a1', 'trivial', 'project', 0.2, 0, 'global',
            '2026-01-01T00:00:00', '2026-01-01T00:00:00')""")
        db.commit()
        from agentmemory._impl import _gate_calibration_score
        score = _gate_calibration_score(db)
        assert score is not None
        assert score > 0  # positive correlation = well-calibrated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_gate_calibration.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `_gate_calibration_score`**

```python
def _gate_calibration_score(db):
    """Pearson correlation between confidence-at-write and recalled_count.
    Positive = gate is well-calibrated. Near-zero or negative = miscalibrated.
    Returns None if insufficient data (< 10 memories)."""
    rows = db.execute("""
        SELECT confidence, recalled_count FROM memories
        WHERE retired_at IS NULL AND recalled_count >= 0
    """).fetchall()
    if len(rows) < 10:
        return None
    confs = [r["confidence"] for r in rows]
    recalls = [r["recalled_count"] for r in rows]
    n = len(confs)
    mean_c = sum(confs) / n
    mean_r = sum(recalls) / n
    cov = sum((c - mean_c) * (r - mean_r) for c, r in zip(confs, recalls)) / n
    std_c = (sum((c - mean_c) ** 2 for c in confs) / n) ** 0.5
    std_r = (sum((r - mean_r) ** 2 for r in recalls) / n) ** 0.5
    if std_c == 0 or std_r == 0:
        return 0.0
    return cov / (std_c * std_r)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/test_gate_calibration.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Wire into `cmd_lint` and `cmd_health`**

Add `gate_calibration` as a metric in the lint/health JSON output. Flag a warning if calibration < 0.1 ("W(m) gate may be miscalibrated").

- [ ] **Step 6: Run lint tests**

Run: `cd ~/agentmemory && .venv/bin/python -m pytest tests/ -k "lint or health" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_gate_calibration.py src/agentmemory/_impl.py
git commit -m "feat: W(m) gate calibration feedback loop

Track Pearson correlation between confidence-at-write and
recalled_count. Surfaces in lint/health as gate_calibration metric.
Flags warning if < 0.1 (miscalibrated gate). Foundation for future
automatic weight retuning of A-MAC factors.

Papers: [ML-7] Dunlosky & Metcalfe 2009, [ML-6] Nelson & Narens 1990"
```

---

## Final Verification

- [ ] **Run the full test suite**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/ -q --timeout=120
```

- [ ] **Run the search quality benchmark**

```bash
cd ~/agentmemory && .venv/bin/python -m tests.bench.run --check
```

- [ ] **Verify migration 037 applies cleanly to fresh DB**

```bash
cd ~/agentmemory && rm -f /tmp/test_fresh.db && \
  .venv/bin/python -c "
from agentmemory.brain import Brain
b = Brain(db_path='/tmp/test_fresh.db', agent_id='test')
b.remember('test', category='lesson')
print(b.doctor())
"
```

- [ ] **Tag release**

```bash
git tag v1.7.0-alpha
```
