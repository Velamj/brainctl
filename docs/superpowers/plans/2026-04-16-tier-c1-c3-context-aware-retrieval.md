# Tier C (v1.8.0): Context-Aware Retrieval — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store encoding context at write time, use context-matching as a retrieval reranking signal, and schedule spaced reviews based on optimal inter-study intervals.

**Architecture:** Two migrations (040-041) add encoding context and review scheduling columns. The context-matching reranker plugs into the existing RRF pipeline as an additional signal. The spaced-review scheduler integrates with the consolidation cycle.

**Tech Stack:** Python 3.11+, SQLite, pytest, hashlib. No new dependencies.

**Spec:** `research/wave14/32_neuroscience_grounded_improvements.md` (Section 8, C1-C3)

---

## Task 1: Encoding Context Snapshot (Migration 040)

Capture a JSON snapshot of the agent's operational context at memory write time + a hash for fast matching.

**Papers:** Tulving & Thomson 1973 [CE-1], Heald et al. 2023 [CE-4], Pink et al. 2025 [CE-14]

**Files:**
- Create: `db/migrations/040_encoding_context.sql`
- Modify: `src/agentmemory/db/init_schema.sql`
- Modify: `src/agentmemory/_impl.py` — add `_build_encoding_context` and `_encoding_context_hash`, wire into cmd_mem_add
- Modify: `src/agentmemory/mcp_server.py` — wire into tool_memory_add
- Create: `tests/test_encoding_context.py`

- [ ] **Step 1: Create migration 040**

Write `db/migrations/040_encoding_context.sql`:
```sql
-- Migration 040: encoding context snapshot (Tulving & Thomson 1973)
-- Capture operational context at memory write time for context-matching retrieval.
ALTER TABLE memories ADD COLUMN encoding_task_context TEXT DEFAULT NULL;
ALTER TABLE memories ADD COLUMN encoding_context_hash TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_memories_context_hash
    ON memories(encoding_context_hash) WHERE encoding_context_hash IS NOT NULL;
```

- [ ] **Step 2: Update init_schema.sql**

Add both columns after `stability` (migration 039's column):
```sql
encoding_task_context TEXT DEFAULT NULL,
encoding_context_hash TEXT DEFAULT NULL,
```

- [ ] **Step 3: Write failing tests**

Create `tests/test_encoding_context.py`:
```python
"""Tests for encoding context snapshot (Tulving & Thomson 1973)."""
import json
import hashlib
import sqlite3
import pathlib
import pytest


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
            encoding_task_context TEXT DEFAULT NULL,
            encoding_context_hash TEXT DEFAULT NULL,
            retired_at TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


class TestBuildEncodingContext:
    def test_captures_project_and_agent(self):
        from agentmemory._impl import _build_encoding_context
        ctx = _build_encoding_context(project="api-v2", agent_id="researcher",
                                       session_id="sess-123")
        parsed = json.loads(ctx)
        assert parsed["project"] == "api-v2"
        assert parsed["agent_id"] == "researcher"
        assert parsed["session_id"] == "sess-123"

    def test_none_fields_omitted(self):
        from agentmemory._impl import _build_encoding_context
        ctx = _build_encoding_context(project=None, agent_id="a1", session_id=None)
        parsed = json.loads(ctx)
        assert "project" not in parsed or parsed["project"] is None
        assert parsed["agent_id"] == "a1"

    def test_returns_json_string(self):
        from agentmemory._impl import _build_encoding_context
        ctx = _build_encoding_context(project="p", agent_id="a")
        assert isinstance(ctx, str)
        json.loads(ctx)  # should not raise


class TestEncodingContextHash:
    def test_deterministic(self):
        from agentmemory._impl import _encoding_context_hash
        h1 = _encoding_context_hash(project="p", agent_id="a", session_id="s")
        h2 = _encoding_context_hash(project="p", agent_id="a", session_id="s")
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        from agentmemory._impl import _encoding_context_hash
        h1 = _encoding_context_hash(project="p1", agent_id="a")
        h2 = _encoding_context_hash(project="p2", agent_id="a")
        assert h1 != h2

    def test_is_hex_string(self):
        from agentmemory._impl import _encoding_context_hash
        h = _encoding_context_hash(project="p", agent_id="a")
        assert isinstance(h, str)
        int(h, 16)  # should not raise
```

- [ ] **Step 4: Implement helper functions**

Add to `src/agentmemory/_impl.py`:
```python
import hashlib as _hashlib

def _build_encoding_context(project=None, agent_id=None, session_id=None,
                             goal=None, active_tool=None):
    """Build a JSON snapshot of the agent's operational context at encoding time.
    Tulving & Thomson 1973: retrieval is best when context matches encoding."""
    ctx = {}
    if project: ctx["project"] = project
    if agent_id: ctx["agent_id"] = agent_id
    if session_id: ctx["session_id"] = session_id
    if goal: ctx["goal"] = goal
    if active_tool: ctx["active_tool"] = active_tool
    return json.dumps(ctx, sort_keys=True)


def _encoding_context_hash(project=None, agent_id=None, session_id=None):
    """SHA-256 hash of project:agent_id:session_id for fast context matching.
    Godden & Baddeley 1975: environment match at retrieval boosts recall."""
    key = f"{project or ''}:{agent_id or ''}:{session_id or ''}"
    return _hashlib.sha256(key.encode()).hexdigest()[:16]
```

- [ ] **Step 5: Wire into memory_add paths**

In both `cmd_mem_add` (_impl.py) and `tool_memory_add` (mcp_server.py), after the INSERT, set encoding context:
```python
try:
    enc_ctx = _build_encoding_context(
        project=getattr(args, 'project', None) or scope_project,
        agent_id=agent_id,
        session_id=getattr(args, 'session_id', None),
    )
    enc_hash = _encoding_context_hash(
        project=getattr(args, 'project', None) or scope_project,
        agent_id=agent_id,
    )
    db.execute("""UPDATE memories SET encoding_task_context = ?,
        encoding_context_hash = ? WHERE id = ?""",
        (enc_ctx, enc_hash, memory_id))
except Exception:
    pass
```

- [ ] **Step 6: Apply migration, run tests, commit**

```bash
sqlite3 db/brain.db < db/migrations/040_encoding_context.sql
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_encoding_context.py -v
git add db/migrations/040_encoding_context.sql src/agentmemory/db/init_schema.sql \
        src/agentmemory/_impl.py src/agentmemory/mcp_server.py tests/test_encoding_context.py
git commit -m "feat: encoding context snapshot (migration 040, Tulving & Thomson 1973)"
```

---

## Task 2: Context-Matching Reranker

Add a context-overlap score to the search reranking pipeline. Memories encoded in a similar context to the current search context get a score boost.

**Papers:** Smith & Vela 2001 [CE-3], Heald et al. 2023 [CE-4], HippoRAG 2024 [CE-11]

**Files:**
- Modify: `src/agentmemory/_impl.py` — add `_context_match_score`, wire into `_apply_recency_and_trim`
- Create: `tests/test_context_reranker.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_context_reranker.py`:
```python
"""Tests for context-matching reranker (Smith & Vela 2001)."""
import json
import pytest


class TestContextMatchScore:
    def test_exact_hash_match_high_score(self):
        """Same context hash should produce maximum context score."""
        from agentmemory._impl import _context_match_score
        score = _context_match_score(
            memory_context='{"project":"api","agent_id":"a1"}',
            memory_hash="abc123",
            current_context='{"project":"api","agent_id":"a1"}',
            current_hash="abc123",
        )
        assert score >= 0.3  # hash match bonus

    def test_no_context_returns_zero(self):
        """Missing context should return 0.0 (no boost)."""
        from agentmemory._impl import _context_match_score
        assert _context_match_score(None, None, None, None) == 0.0

    def test_partial_overlap_positive(self):
        """Shared keys should produce positive score."""
        from agentmemory._impl import _context_match_score
        score = _context_match_score(
            memory_context='{"project":"api","agent_id":"a1"}',
            memory_hash="abc",
            current_context='{"project":"api","agent_id":"a2"}',
            current_hash="def",
        )
        assert score > 0.0  # project matches

    def test_no_overlap_zero(self):
        """Completely different contexts should produce 0.0."""
        from agentmemory._impl import _context_match_score
        score = _context_match_score(
            memory_context='{"project":"billing"}',
            memory_hash="abc",
            current_context='{"project":"auth"}',
            current_hash="def",
        )
        assert score == 0.0

    def test_score_bounded_zero_one(self):
        """Score always in [0, 1]."""
        from agentmemory._impl import _context_match_score
        score = _context_match_score(
            '{"project":"x","agent_id":"a","session_id":"s","goal":"g"}',
            "abc",
            '{"project":"x","agent_id":"a","session_id":"s","goal":"g"}',
            "abc",
        )
        assert 0.0 <= score <= 1.0
```

- [ ] **Step 2: Implement `_context_match_score`**

```python
def _context_match_score(memory_context, memory_hash, current_context, current_hash):
    """Context-matching score for retrieval reranking (Smith & Vela 2001).
    Hash match gives a strong bonus. Key-value overlap gives partial credit."""
    if not memory_context or not current_context:
        return 0.0
    score = 0.0
    if memory_hash and current_hash and memory_hash == current_hash:
        score += 0.3
    try:
        mem_ctx = json.loads(memory_context) if isinstance(memory_context, str) else {}
        cur_ctx = json.loads(current_context) if isinstance(current_context, str) else {}
    except (json.JSONDecodeError, TypeError):
        return score
    if not mem_ctx or not cur_ctx:
        return score
    matching_keys = 0
    total_keys = len(set(mem_ctx.keys()) | set(cur_ctx.keys()))
    for k in mem_ctx:
        if k in cur_ctx and mem_ctx[k] == cur_ctx[k]:
            matching_keys += 1
    if total_keys > 0:
        score += 0.7 * (matching_keys / total_keys)
    return min(1.0, score)
```

- [ ] **Step 3: Wire into search scoring**

In `_apply_recency_and_trim`, after computing the main score for each result, add the context-match signal. The search needs to know the current context — derive it from `args.agent`, `args.project` (if available on the argparse namespace), and pass through to scoring.

Add `encoding_task_context` and `encoding_context_hash` to the SELECT column lists in `_fts_memories()` and `_vec_memories()`.

```python
# In the scoring loop:
ctx_score = _context_match_score(
    r.get("encoding_task_context"), r.get("encoding_context_hash"),
    current_context_json, current_context_hash,
)
final_score *= (1.0 + 0.2 * ctx_score)  # up to 20% boost
```

- [ ] **Step 4: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_context_reranker.py -v
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_brain.py tests/test_brain_enhanced.py -q
git add src/agentmemory/_impl.py tests/test_context_reranker.py
git commit -m "feat: context-matching reranker (Smith & Vela 2001, Heald et al. 2023)"
```

---

## Task 3: Spaced-Review Scheduler (Migration 041)

Compute optimal replay intervals from temporal_class and schedule memories for review. Integrates with the consolidation cycle.

**Papers:** Cepeda et al. 2006 [CE-7], Murre & Dros 2015 [CE-8]

**Files:**
- Create: `db/migrations/041_spaced_review.sql`
- Modify: `src/agentmemory/db/init_schema.sql`
- Modify: `src/agentmemory/hippocampus.py` — add `schedule_spaced_reviews` and `process_due_reviews`
- Create: `tests/test_spaced_review.py`

- [ ] **Step 1: Create migration 041**

```sql
-- Migration 041: spaced-review scheduler (Cepeda et al. 2006)
-- Optimal inter-study interval is ~15% of desired retention interval.
ALTER TABLE memories ADD COLUMN next_review_at TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_memories_next_review
    ON memories(next_review_at) WHERE next_review_at IS NOT NULL AND retired_at IS NULL;
```

- [ ] **Step 2: Update init_schema.sql**

Add `next_review_at TEXT DEFAULT NULL` after `encoding_context_hash`.

- [ ] **Step 3: Write failing tests**

Create `tests/test_spaced_review.py`:
```python
"""Tests for spaced-review scheduler (Cepeda et al. 2006)."""
import sqlite3
import pytest
from datetime import datetime, timedelta


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'test',
            content TEXT NOT NULL,
            category TEXT DEFAULT 'lesson',
            scope TEXT DEFAULT 'global',
            confidence REAL DEFAULT 0.5,
            temporal_class TEXT DEFAULT 'medium',
            stability REAL DEFAULT 1.0,
            recalled_count INTEGER DEFAULT 0,
            next_review_at TEXT DEFAULT NULL,
            retired_at TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


def _insert(db, content="test", temporal_class="medium", next_review_at=None,
            confidence=0.5):
    db.execute(
        """INSERT INTO memories (content, temporal_class, next_review_at, confidence)
           VALUES (?, ?, ?, ?)""",
        (content, temporal_class, next_review_at, confidence))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestComputeReviewInterval:
    def test_ephemeral_short_interval(self):
        from agentmemory.hippocampus import compute_review_interval_hours
        hours = compute_review_interval_hours("ephemeral", stability=1.0)
        assert hours < 24  # under a day

    def test_long_class_weeks_interval(self):
        from agentmemory.hippocampus import compute_review_interval_hours
        hours = compute_review_interval_hours("long", stability=1.0)
        assert hours > 24 * 7  # over a week

    def test_high_stability_longer_interval(self):
        from agentmemory.hippocampus import compute_review_interval_hours
        low = compute_review_interval_hours("medium", stability=1.0)
        high = compute_review_interval_hours("medium", stability=5.0)
        assert high > low


class TestScheduleReviews:
    def test_schedules_unscheduled_memories(self):
        db = _make_db()
        _insert(db, next_review_at=None, confidence=0.6)
        from agentmemory.hippocampus import schedule_spaced_reviews
        stats = schedule_spaced_reviews(db)
        row = db.execute("SELECT next_review_at FROM memories WHERE id=1").fetchone()
        assert row["next_review_at"] is not None
        assert stats["scheduled"] >= 1

    def test_skips_already_scheduled(self):
        db = _make_db()
        _insert(db, next_review_at="2099-01-01T00:00:00", confidence=0.6)
        from agentmemory.hippocampus import schedule_spaced_reviews
        stats = schedule_spaced_reviews(db)
        assert stats["scheduled"] == 0


class TestProcessDueReviews:
    def test_replays_due_memories(self):
        db = _make_db()
        past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        _insert(db, next_review_at=past, confidence=0.6)
        from agentmemory.hippocampus import process_due_reviews
        stats = process_due_reviews(db)
        assert stats["reviewed"] >= 1

    def test_skips_future_reviews(self):
        db = _make_db()
        future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
        _insert(db, next_review_at=future, confidence=0.6)
        from agentmemory.hippocampus import process_due_reviews
        stats = process_due_reviews(db)
        assert stats["reviewed"] == 0

    def test_reschedules_after_review(self):
        db = _make_db()
        past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        mid = _insert(db, next_review_at=past, confidence=0.6)
        from agentmemory.hippocampus import process_due_reviews
        process_due_reviews(db)
        row = db.execute("SELECT next_review_at FROM memories WHERE id=?", (mid,)).fetchone()
        assert row["next_review_at"] is not None
        assert row["next_review_at"] > past  # rescheduled to future
```

- [ ] **Step 4: Implement**

Add to `src/agentmemory/hippocampus.py`:
```python
def compute_review_interval_hours(temporal_class, stability=1.0):
    """Compute optimal inter-study interval in hours (Cepeda et al. 2006).
    ISI ≈ 15% of retention interval, scaled by stability."""
    ri_days = _RETENTION_INTERVALS.get(temporal_class, 23.0)
    base_isi_hours = ri_days * 0.15 * 24  # 15% of RI in hours
    return base_isi_hours * max(0.5, min(10.0, stability))


def schedule_spaced_reviews(db, min_confidence=0.3):
    """Schedule next_review_at for active memories that lack one."""
    rows = db.execute("""
        SELECT id, temporal_class, stability FROM memories
        WHERE retired_at IS NULL AND next_review_at IS NULL
        AND confidence >= ?
    """, (min_confidence,)).fetchall()
    scheduled = 0
    for r in rows:
        hours = compute_review_interval_hours(
            r["temporal_class"] or "medium",
            r["stability"] or 1.0,
        )
        db.execute("""UPDATE memories SET next_review_at =
            strftime('%Y-%m-%dT%H:%M:%S', 'now', ? || ' hours')
            WHERE id = ?""", (str(int(hours)), r["id"]))
        scheduled += 1
    db.commit()
    return {"scheduled": scheduled}


def process_due_reviews(db):
    """Process memories whose next_review_at has passed.
    Increments recalled_count (replay) and reschedules."""
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    rows = db.execute("""
        SELECT id, temporal_class, stability FROM memories
        WHERE retired_at IS NULL AND next_review_at IS NOT NULL
        AND next_review_at <= ?
    """, (now,)).fetchall()
    reviewed = 0
    for r in rows:
        db.execute("""UPDATE memories SET
            recalled_count = recalled_count + 1,
            last_recalled_at = strftime('%Y-%m-%dT%H:%M:%S', 'now')
            WHERE id = ?""", (r["id"],))
        hours = compute_review_interval_hours(
            r["temporal_class"] or "medium",
            r["stability"] or 1.0,
        )
        db.execute("""UPDATE memories SET next_review_at =
            strftime('%Y-%m-%dT%H:%M:%S', 'now', ? || ' hours')
            WHERE id = ?""", (str(int(hours)), r["id"]))
        reviewed += 1
    db.commit()
    return {"reviewed": reviewed}
```

- [ ] **Step 5: Apply migration, run tests, commit**

```bash
sqlite3 db/brain.db < db/migrations/041_spaced_review.sql
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_spaced_review.py -v
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_consolidation_v2.py tests/test_consolidation.py -v
git add db/migrations/041_spaced_review.sql src/agentmemory/db/init_schema.sql \
        src/agentmemory/hippocampus.py tests/test_spaced_review.py
git commit -m "feat: spaced-review scheduler (migration 041, Cepeda et al. 2006)"
```

---

## Final Verification

- [ ] **Run all new v1.8.0 tests**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_encoding_context.py tests/test_context_reranker.py tests/test_spaced_review.py -v
```

- [ ] **Run full regression suite**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_brain.py tests/test_brain_enhanced.py tests/test_consolidation.py tests/test_consolidation_v2.py -q
```

- [ ] **Run bench harness**

```bash
cd ~/agentmemory && .venv/bin/python -m tests.bench.run --check
```

- [ ] **Verify migrations 040-041 apply cleanly**

```bash
rm -f /tmp/test_v180.db
sqlite3 /tmp/test_v180.db < src/agentmemory/db/init_schema.sql
sqlite3 /tmp/test_v180.db "PRAGMA table_info(memories)" | grep -E "encoding_task|encoding_context|next_review"
```
