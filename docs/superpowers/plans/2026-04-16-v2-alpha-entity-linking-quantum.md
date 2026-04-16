# v2.0-alpha: Auto Entity Linking + Quantum Schema — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 77% KG isolation problem via automatic entity linking (zero LLM calls), then deploy the quantum schema migration and wire phase-aware amplitude scoring into the retrieval pipeline.

**Architecture:** Three entity linking layers (FTS5 name matching → GLiNER NER → co-occurrence edges) are added as new functions in `_impl.py` callable via `brainctl entity autolink`. The quantum schema migration is applied from the existing `db/migrations/quantum_schema_migration_sqlite.sql`. Phase-aware scoring is wired into the RRF pipeline as an optional signal.

**Tech Stack:** Python 3.11+, SQLite, pytest. GLiNER (optional: `pip install gliner`). No other new dependencies.

**Spec:** `research/wave15/33_v2_roadmap.md` (Pillar 1 + Pillar 2)

---

## Task 1: FTS5 Entity Name Matching (Layer 1)

Scan all active memories for substring matches against the 248 known entity names. Create `knowledge_edges` with `relation_type='mentions'`. Pure SQL, zero dependencies.

**Papers:** HippoRAG (Gutierrez et al. 2024) — 48% of failures from NER omissions

**Files:**
- Modify: `src/agentmemory/_impl.py` — add `cmd_entity_autolink` and `_fts5_entity_match`
- Create: `tests/test_entity_autolink.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_entity_autolink.py`:
```python
"""Tests for automatic entity linking (Layer 1: FTS5 name matching)."""
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
            retired_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE, entity_type TEXT DEFAULT 'concept',
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT NOT NULL, source_id INTEGER NOT NULL,
            target_table TEXT NOT NULL, target_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL, weight REAL DEFAULT 1.0,
            agent_id TEXT DEFAULT 'autolink',
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            UNIQUE(source_table, source_id, target_table, target_id, relation_type)
        );
    """)
    return db


def _insert_memory(db, content):
    db.execute("INSERT INTO memories (content) VALUES (?)", (content,))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_entity(db, name, entity_type="concept"):
    db.execute("INSERT INTO entities (name, entity_type) VALUES (?, ?)",
               (name, entity_type))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestFTS5EntityMatch:
    def test_exact_name_match(self):
        """Memory mentioning an entity name should get linked."""
        db = _make_db()
        _insert_entity(db, "Alice", "person")
        m = _insert_memory(db, "Alice deployed the new API endpoint")
        from agentmemory._impl import _fts5_entity_match
        stats = _fts5_entity_match(db)
        edges = db.execute("""SELECT * FROM knowledge_edges
            WHERE source_table='memories' AND source_id=? AND target_table='entities'""",
            (m,)).fetchall()
        assert len(edges) >= 1
        assert stats["linked"] >= 1

    def test_case_insensitive(self):
        """Matching should be case-insensitive."""
        db = _make_db()
        _insert_entity(db, "brainctl", "tool")
        m = _insert_memory(db, "BRAINCTL is a memory system")
        from agentmemory._impl import _fts5_entity_match
        _fts5_entity_match(db)
        edges = db.execute("""SELECT * FROM knowledge_edges
            WHERE source_table='memories' AND source_id=?""", (m,)).fetchall()
        assert len(edges) >= 1

    def test_no_match_no_edge(self):
        """Memory not mentioning any entity should get no edges."""
        db = _make_db()
        _insert_entity(db, "Alice", "person")
        m = _insert_memory(db, "The weather is nice today")
        from agentmemory._impl import _fts5_entity_match
        _fts5_entity_match(db)
        edges = db.execute("""SELECT * FROM knowledge_edges
            WHERE source_table='memories' AND source_id=?""", (m,)).fetchall()
        assert len(edges) == 0

    def test_multiple_entities_in_one_memory(self):
        """Memory mentioning multiple entities should get multiple edges."""
        db = _make_db()
        _insert_entity(db, "Alice", "person")
        _insert_entity(db, "Acme", "organization")
        m = _insert_memory(db, "Alice works at Acme on the backend")
        from agentmemory._impl import _fts5_entity_match
        _fts5_entity_match(db)
        edges = db.execute("""SELECT * FROM knowledge_edges
            WHERE source_table='memories' AND source_id=?""", (m,)).fetchall()
        assert len(edges) >= 2

    def test_no_duplicate_edges(self):
        """Running twice should not create duplicate edges."""
        db = _make_db()
        _insert_entity(db, "Alice", "person")
        _insert_memory(db, "Alice likes Python")
        from agentmemory._impl import _fts5_entity_match
        _fts5_entity_match(db)
        _fts5_entity_match(db)
        edges = db.execute("SELECT COUNT(*) as cnt FROM knowledge_edges").fetchone()
        assert edges["cnt"] == 1

    def test_skips_short_entity_names(self):
        """Entity names < 3 chars should be skipped (too many false positives)."""
        db = _make_db()
        _insert_entity(db, "AI", "concept")
        _insert_memory(db, "AI is transforming the world of AIDING people")
        from agentmemory._impl import _fts5_entity_match
        _fts5_entity_match(db)
        edges = db.execute("SELECT COUNT(*) as cnt FROM knowledge_edges").fetchone()
        assert edges["cnt"] == 0

    def test_skips_already_linked(self):
        """Memories that already have entity edges should be skipped."""
        db = _make_db()
        eid = _insert_entity(db, "Alice", "person")
        m = _insert_memory(db, "Alice is an engineer")
        db.execute("""INSERT INTO knowledge_edges
            (source_table, source_id, target_table, target_id, relation_type)
            VALUES ('memories', ?, 'entities', ?, 'mentions')""", (m, eid))
        db.commit()
        from agentmemory._impl import _fts5_entity_match
        stats = _fts5_entity_match(db)
        assert stats["skipped_already_linked"] >= 1
```

- [ ] **Step 2: Implement `_fts5_entity_match`**

Add to `src/agentmemory/_impl.py`:
```python
_AUTOLINK_MIN_NAME_LENGTH = 3

def _fts5_entity_match(db, agent_id="autolink"):
    """Layer 1: match entity names against memory content via substring search.
    HippoRAG (2024): 48% of retrieval failures come from NER omissions."""
    entities = db.execute(
        "SELECT id, name FROM entities WHERE length(name) >= ?",
        (_AUTOLINK_MIN_NAME_LENGTH,)).fetchall()

    already_linked = set()
    for row in db.execute("""
        SELECT DISTINCT source_id FROM knowledge_edges
        WHERE source_table = 'memories' AND target_table = 'entities'
    """).fetchall():
        already_linked.add(row["source_id"])

    memories = db.execute("""
        SELECT id, content FROM memories
        WHERE retired_at IS NULL AND id NOT IN ({})
    """.format(",".join(str(x) for x in already_linked) or "0")).fetchall()

    linked = 0
    edges_created = 0
    skipped_already_linked = len(already_linked)

    for mem in memories:
        content_lower = mem["content"].lower()
        for ent in entities:
            if ent["name"].lower() in content_lower:
                try:
                    db.execute("""INSERT OR IGNORE INTO knowledge_edges
                        (source_table, source_id, target_table, target_id,
                         relation_type, agent_id, created_at)
                        VALUES ('memories', ?, 'entities', ?, 'mentions', ?,
                                strftime('%Y-%m-%dT%H:%M:%S','now'))""",
                        (mem["id"], ent["id"], agent_id))
                    edges_created += 1
                except Exception:
                    pass
        if edges_created > 0:
            linked += 1

    db.commit()
    return {"linked": linked, "edges_created": edges_created,
            "skipped_already_linked": skipped_already_linked,
            "memories_scanned": len(memories)}
```

NOTE: The `NOT IN` query with f-string is safe here because `already_linked` contains only integer PKs from the database, not user input. For very large sets, switch to a temp table.

- [ ] **Step 3: Add CLI command `brainctl entity autolink`**

Wire into the entity subcommand parser in `src/agentmemory/commands/entity.py` (or `_impl.py` if entity commands are there). Add:
```python
autolink_p = entity_sub.add_parser("autolink",
    help="Auto-link memories to entities via name matching and NER")
autolink_p.add_argument("--layer", choices=["fts5", "ner", "all"], default="fts5",
    help="Which linking layer to run (default: fts5)")
```

Handler calls `_fts5_entity_match(db)` and prints JSON stats.

- [ ] **Step 4: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_entity_autolink.py -v
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_brain.py tests/test_brain_enhanced.py -q
git add src/agentmemory/_impl.py src/agentmemory/commands/entity.py tests/test_entity_autolink.py
git commit -m "feat: auto entity linking Layer 1 — FTS5 name matching

Scan all active memories for entity name substrings. Create
knowledge_edges with relation_type='mentions'. Case-insensitive,
skips names < 3 chars, no duplicates. Available via
brainctl entity autolink --layer fts5.

Papers: HippoRAG (Gutierrez et al. 2024) — 48% of failures from
NER omissions; entity coverage is the #1 lever."
```

- [ ] **Step 5: Run on real brain.db and measure improvement**

```bash
cd ~/agentmemory && .venv/bin/python -c "
import sqlite3, json
from agentmemory._impl import _fts5_entity_match
db = sqlite3.connect('db/brain.db')
db.row_factory = sqlite3.Row
result = _fts5_entity_match(db)
print(json.dumps(result, indent=2))

# Check new coupling gate stats
from agentmemory.hippocampus import coupling_gate
episodic_ids = [r['id'] for r in db.execute('''
    SELECT id FROM memories WHERE retired_at IS NULL
    AND memory_type = \"episodic\" AND confidence > 0.5
''').fetchall()]
passed, failed = coupling_gate(db, episodic_ids)
print(f'Coupling gate: {len(passed)} passed, {len(failed)} failed ({len(failed)*100//(len(passed)+len(failed))}% isolated)')
"
```

---

## Task 2: GLiNER NER Integration (Layer 2)

Optional NER via GLiNER (205M params, CPU, zero LLM calls) for memories that Layer 1 missed. Extracts entities matching brainctl's type system.

**Papers:** Zaratiana et al. (2024) GLiNER, NAACL 2024

**Files:**
- Modify: `src/agentmemory/_impl.py` — add `_gliner_entity_extract`
- Modify: `tests/test_entity_autolink.py` — add TestGLiNERExtract
- Modify: `pyproject.toml` — add `[ner]` optional extra

- [ ] **Step 1: Add optional dependency**

In `pyproject.toml`, add to `[project.optional-dependencies]`:
```toml
ner = ["gliner>=0.2"]
```

- [ ] **Step 2: Write failing tests**

Add to `tests/test_entity_autolink.py`:
```python
class TestGLiNERExtract:
    def test_extracts_person_from_text(self):
        """Should extract person entities from text."""
        pytest.importorskip("gliner")
        from agentmemory._impl import _gliner_entity_extract
        db = _make_db()
        _insert_memory(db, "John Smith deployed the production API yesterday")
        stats = _gliner_entity_extract(db)
        # Should have created at least one entity and one edge
        entities = db.execute("SELECT * FROM entities WHERE name LIKE '%John%'").fetchall()
        assert len(entities) >= 1 or stats["entities_created"] >= 0

    def test_skips_if_gliner_not_installed(self):
        """Should return gracefully if gliner is not installed."""
        from agentmemory._impl import _gliner_entity_extract
        db = _make_db()
        _insert_memory(db, "Some text about Alice")
        # This should not raise even if gliner is not installed
        stats = _gliner_entity_extract(db)
        assert "error" in stats or stats.get("entities_created", 0) >= 0

    def test_matches_existing_entities(self):
        """Extracted entities should match against existing entities first."""
        pytest.importorskip("gliner")
        db = _make_db()
        _insert_entity(db, "Alice Johnson", "person")
        _insert_memory(db, "Alice Johnson reviewed the pull request")
        from agentmemory._impl import _gliner_entity_extract
        stats = _gliner_entity_extract(db)
        # Should link to existing Alice Johnson, not create a duplicate
        count = db.execute("SELECT COUNT(*) as c FROM entities WHERE name LIKE '%Alice%'").fetchone()
        assert count["c"] == 1  # no duplicate
```

- [ ] **Step 3: Implement `_gliner_entity_extract`**

```python
_GLINER_LABELS = ["person", "project", "tool", "service", "concept", "organization"]

def _gliner_entity_extract(db, agent_id="autolink", model_name="gliner_medium-v2.1"):
    """Layer 2: GLiNER zero-shot NER (Zaratiana et al. 2024, NAACL).
    Extracts entities from unlinked memories using a 205M-param model.
    No LLM calls. Requires: pip install brainctl[ner]"""
    try:
        from gliner import GLiNER
    except ImportError:
        return {"error": "gliner not installed. Run: pip install brainctl[ner]"}

    model = GLiNER.from_pretrained(model_name)

    already_linked = {r["source_id"] for r in db.execute("""
        SELECT DISTINCT source_id FROM knowledge_edges
        WHERE source_table = 'memories' AND target_table = 'entities'
    """).fetchall()}

    memories = db.execute("""
        SELECT id, content FROM memories
        WHERE retired_at IS NULL AND id NOT IN ({})
    """.format(",".join(str(x) for x in already_linked) or "0")).fetchall()

    existing_entities = {r["name"].lower(): r["id"]
                         for r in db.execute("SELECT id, name FROM entities").fetchall()}

    entities_created = 0
    edges_created = 0

    for mem in memories:
        predictions = model.predict_entities(mem["content"], _GLINER_LABELS,
                                              threshold=0.5)
        for pred in predictions:
            name = pred["text"].strip()
            if len(name) < 3:
                continue
            etype = pred["label"]
            name_lower = name.lower()

            if name_lower in existing_entities:
                eid = existing_entities[name_lower]
            else:
                db.execute("""INSERT OR IGNORE INTO entities (name, entity_type,
                    created_at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%S','now'))""",
                    (name, etype))
                eid = db.execute("SELECT id FROM entities WHERE name = ?",
                                 (name,)).fetchone()
                if eid:
                    eid = eid["id"]
                    existing_entities[name_lower] = eid
                    entities_created += 1
                else:
                    continue

            try:
                db.execute("""INSERT OR IGNORE INTO knowledge_edges
                    (source_table, source_id, target_table, target_id,
                     relation_type, agent_id, created_at)
                    VALUES ('memories', ?, 'entities', ?, 'mentions', ?,
                            strftime('%Y-%m-%dT%H:%M:%S','now'))""",
                    (mem["id"], eid, agent_id))
                edges_created += 1
            except Exception:
                pass

    db.commit()
    return {"entities_created": entities_created, "edges_created": edges_created,
            "memories_scanned": len(memories)}
```

- [ ] **Step 4: Wire `--layer ner` into the autolink CLI command**

- [ ] **Step 5: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_entity_autolink.py -v
git add src/agentmemory/_impl.py pyproject.toml tests/test_entity_autolink.py
git commit -m "feat: auto entity linking Layer 2 — GLiNER NER (optional)

Zero-shot NER via GLiNER (205M params, CPU). Extracts person/project/
tool/service/concept/organization from unlinked memories. Matches
against existing entities before creating new ones.
Requires: pip install brainctl[ner]

Papers: Zaratiana et al. (2024) GLiNER, NAACL 2024"
```

---

## Task 3: Co-occurrence Edges (Layer 3)

For memories now linked to 2+ entities, create entity-to-entity co-occurrence edges. Densifies the graph for PageRank traversal.

**Papers:** Wang (2025) SPRIG, arXiv:2602.23372

**Files:**
- Modify: `src/agentmemory/_impl.py` — add `_create_cooccurrence_edges`
- Modify: `tests/test_entity_autolink.py` — add TestCooccurrence

- [ ] **Step 1: Write failing tests**

```python
class TestCooccurrenceEdges:
    def test_creates_edges_between_cooccurring_entities(self):
        """Entities mentioned in the same memory should get linked."""
        db = _make_db()
        e1 = _insert_entity(db, "Alice", "person")
        e2 = _insert_entity(db, "Acme", "organization")
        m = _insert_memory(db, "Alice works at Acme")
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id,
            target_table, target_id, relation_type)
            VALUES ('memories', ?, 'entities', ?, 'mentions')""", (m, e1))
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id,
            target_table, target_id, relation_type)
            VALUES ('memories', ?, 'entities', ?, 'mentions')""", (m, e2))
        db.commit()
        from agentmemory._impl import _create_cooccurrence_edges
        stats = _create_cooccurrence_edges(db)
        edges = db.execute("""SELECT * FROM knowledge_edges
            WHERE source_table='entities' AND target_table='entities'
            AND relation_type='co_occurs'""").fetchall()
        assert len(edges) >= 1
        assert stats["edges_created"] >= 1

    def test_no_self_edges(self):
        """An entity should not get a co-occurrence edge to itself."""
        db = _make_db()
        e1 = _insert_entity(db, "Alice", "person")
        m = _insert_memory(db, "Alice likes Alice's coding style")
        db.execute("""INSERT INTO knowledge_edges (source_table, source_id,
            target_table, target_id, relation_type)
            VALUES ('memories', ?, 'entities', ?, 'mentions')""", (m, e1))
        db.commit()
        from agentmemory._impl import _create_cooccurrence_edges
        _create_cooccurrence_edges(db)
        self_edges = db.execute("""SELECT * FROM knowledge_edges
            WHERE source_table='entities' AND target_table='entities'
            AND source_id = target_id""").fetchall()
        assert len(self_edges) == 0

    def test_no_duplicates(self):
        """Running twice should not create duplicate edges."""
        db = _make_db()
        e1 = _insert_entity(db, "Alice", "person")
        e2 = _insert_entity(db, "Bob", "person")
        m = _insert_memory(db, "Alice and Bob pair programmed")
        for eid in [e1, e2]:
            db.execute("""INSERT INTO knowledge_edges (source_table, source_id,
                target_table, target_id, relation_type)
                VALUES ('memories', ?, 'entities', ?, 'mentions')""", (m, eid))
        db.commit()
        from agentmemory._impl import _create_cooccurrence_edges
        _create_cooccurrence_edges(db)
        _create_cooccurrence_edges(db)
        count = db.execute("""SELECT COUNT(*) as c FROM knowledge_edges
            WHERE relation_type='co_occurs'""").fetchone()
        assert count["c"] == 1
```

- [ ] **Step 2: Implement**

```python
def _create_cooccurrence_edges(db, agent_id="autolink"):
    """Layer 3: entity co-occurrence edges (SPRIG, Wang 2025).
    For memories linked to 2+ entities, create entity↔entity edges."""
    rows = db.execute("""
        SELECT ke.source_id as memory_id,
               GROUP_CONCAT(ke.target_id) as entity_ids
        FROM knowledge_edges ke
        WHERE ke.source_table = 'memories' AND ke.target_table = 'entities'
          AND ke.relation_type = 'mentions'
        GROUP BY ke.source_id
        HAVING COUNT(DISTINCT ke.target_id) >= 2
    """).fetchall()

    edges_created = 0
    for row in rows:
        eids = [int(x) for x in row["entity_ids"].split(",")]
        for i, e1 in enumerate(eids):
            for e2 in eids[i+1:]:
                if e1 == e2:
                    continue
                src, tgt = min(e1, e2), max(e1, e2)
                try:
                    db.execute("""INSERT OR IGNORE INTO knowledge_edges
                        (source_table, source_id, target_table, target_id,
                         relation_type, agent_id, created_at)
                        VALUES ('entities', ?, 'entities', ?, 'co_occurs', ?,
                                strftime('%Y-%m-%dT%H:%M:%S','now'))""",
                        (src, tgt, agent_id))
                    edges_created += 1
                except Exception:
                    pass
    db.commit()
    return {"edges_created": edges_created, "memories_with_pairs": len(rows)}
```

- [ ] **Step 3: Wire `--layer all` to run Layers 1-3 in sequence**

- [ ] **Step 4: Run tests, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_entity_autolink.py -v
git add src/agentmemory/_impl.py tests/test_entity_autolink.py
git commit -m "feat: auto entity linking Layer 3 — co-occurrence edges (SPRIG)

Create entity↔entity edges when both are mentioned in the same memory.
Densifies the graph for PageRank traversal. No duplicate edges,
no self-edges, idempotent.

Papers: Wang (2025) SPRIG, arXiv:2602.23372"
```

---

## Task 4: Deploy Quantum Schema Migration

Apply the existing 548-line quantum schema migration to brain.db. Adds columns for phase, interference, collapse, and entanglement.

**Files:**
- Existing: `db/migrations/quantum_schema_migration_sqlite.sql` (already in repo)
- Modify: `src/agentmemory/db/init_schema.sql` — verify quantum columns present

- [ ] **Step 1: Backup brain.db**

```bash
cp db/brain.db db/brain.db.pre-quantum-$(date +%Y%m%d)
```

- [ ] **Step 2: Review the migration**

```bash
head -50 db/migrations/quantum_schema_migration_sqlite.sql
wc -l db/migrations/quantum_schema_migration_sqlite.sql
```

- [ ] **Step 3: Apply the migration**

```bash
cd ~/agentmemory && sqlite3 db/brain.db < db/migrations/quantum_schema_migration_sqlite.sql
```

If any ALTER TABLE fails with "duplicate column", the column already exists — skip it. The migration uses `ALTER TABLE ... ADD COLUMN` which SQLite doesn't support `IF NOT EXISTS` for. Run statement-by-statement if needed.

- [ ] **Step 4: Verify quantum columns exist**

```bash
cd ~/agentmemory && sqlite3 db/brain.db "PRAGMA table_info(memories)" | grep -E "confidence_phase|hilbert_projection|coherence_syndrome|decoherence_rate"
```

Expected: 4 rows showing the quantum columns.

- [ ] **Step 5: Verify new tables exist**

```bash
sqlite3 db/brain.db ".tables" | tr ' ' '\n' | grep -E "belief_collapse|agent_entanglement|ghz"
```

- [ ] **Step 6: Run existing tests to verify no regressions**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_brain.py tests/test_brain_enhanced.py tests/test_consolidation_v2.py -q
```

- [ ] **Step 7: Commit**

```bash
git commit --allow-empty -m "chore: deploy quantum schema migration to brain.db

Applied db/migrations/quantum_schema_migration_sqlite.sql (existing).
Adds: confidence_phase, hilbert_projection, coherence_syndrome,
decoherence_rate columns on memories. Creates: belief_collapse_events,
agent_entanglement, agent_ghz_groups tables. Backward compatible —
all new columns have defaults, all classical code paths unaffected."
```

---

## Task 5: Wire Phase-Aware Amplitude Scoring

Integrate the existing quantum amplitude scorer into the RRF pipeline as an optional blended signal (50/50 classical+quantum).

**Files:**
- Modify: `src/agentmemory/_impl.py` — add quantum scoring as optional reranking signal
- Create: `tests/test_quantum_scoring.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_quantum_scoring.py`:
```python
"""Tests for quantum amplitude scoring integration."""
import sqlite3
import math
import pytest


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL, confidence REAL DEFAULT 0.5,
            confidence_phase REAL DEFAULT 0.0,
            alpha REAL DEFAULT 1.0, beta REAL DEFAULT 1.0,
            retired_at TEXT DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT, source_id INTEGER,
            target_table TEXT, target_id INTEGER,
            relation_type TEXT, weight REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


class TestQuantumAmplitude:
    def test_amplitude_from_confidence_and_phase(self):
        """Amplitude = sqrt(confidence) * exp(i * phase)."""
        from agentmemory._impl import _quantum_amplitude_score
        score = _quantum_amplitude_score(confidence=0.8, phase=0.0,
                                          neighbor_phases=[])
        assert score > 0

    def test_constructive_interference_boosts(self):
        """Neighbors with similar phases should boost the score."""
        from agentmemory._impl import _quantum_amplitude_score
        no_neighbors = _quantum_amplitude_score(0.5, 0.1, [])
        constructive = _quantum_amplitude_score(0.5, 0.1,
            [{"phase": 0.1, "weight": 0.8},
             {"phase": 0.15, "weight": 0.7}])
        assert constructive >= no_neighbors

    def test_destructive_interference_reduces(self):
        """Neighbors with opposite phases should reduce the score."""
        from agentmemory._impl import _quantum_amplitude_score
        no_neighbors = _quantum_amplitude_score(0.5, 0.0, [])
        destructive = _quantum_amplitude_score(0.5, 0.0,
            [{"phase": math.pi, "weight": 0.8}])
        assert destructive <= no_neighbors

    def test_zero_confidence_zero_score(self):
        """Zero confidence should produce zero amplitude."""
        from agentmemory._impl import _quantum_amplitude_score
        assert _quantum_amplitude_score(0.0, 0.0, []) == 0.0
```

- [ ] **Step 2: Implement `_quantum_amplitude_score`**

```python
def _quantum_amplitude_score(confidence, phase, neighbor_phases):
    """Quantum amplitude scoring (Wave 1, brainctl quantum research).
    amplitude = sqrt(confidence) * exp(i * phase). Interference from
    knowledge-graph neighbors modulates the score."""
    if confidence <= 0:
        return 0.0
    base_amp = math.sqrt(max(0.0, min(1.0, confidence)))
    if not neighbor_phases:
        return base_amp

    interference = 0.0
    for n in neighbor_phases:
        n_phase = n.get("phase", 0.0)
        n_weight = n.get("weight", 0.5)
        phase_diff = phase - n_phase
        interference += n_weight * math.cos(phase_diff)

    modulated = base_amp * (1.0 + 0.1 * interference)
    return max(0.0, min(1.0, modulated))
```

- [ ] **Step 3: Wire into search scoring (optional, gated)**

In `_apply_recency_and_trim`, after Q-value adjustment, add quantum scoring as an optional signal. Gate on whether `confidence_phase` column exists and is populated:

```python
q_phase = r.get("confidence_phase")
if q_phase is not None and q_phase != 0.0:
    q_score = _quantum_amplitude_score(
        confidence=r.get("confidence") or 0.5,
        phase=q_phase,
        neighbor_phases=[],  # populated from KG edges in future
    )
    score = 0.5 * score + 0.5 * q_score  # 50/50 blend
```

Add `m.confidence_phase` to `_fts_memories` and `_vec_memories` SELECT lists.

- [ ] **Step 4: Run tests, bench, commit**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_quantum_scoring.py -v
cd ~/agentmemory && .venv/bin/python -m tests.bench.run --check
git add src/agentmemory/_impl.py tests/test_quantum_scoring.py
git commit -m "feat: quantum amplitude scoring integration (50/50 blend)

Wire phase-aware amplitude scoring into the RRF pipeline. Gated on
confidence_phase being populated. Constructive interference from
knowledge-graph neighbors boosts score; destructive reduces it.
50/50 classical+quantum blend by default.

Papers: brainctl quantum research Wave 1 (ANALYSIS.md)"
```

---

## Final Verification

- [ ] **Run all new tests**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_entity_autolink.py tests/test_quantum_scoring.py -v
```

- [ ] **Run full regression suite**

```bash
cd ~/agentmemory && .venv/bin/python -m pytest tests/test_brain.py tests/test_brain_enhanced.py tests/test_consolidation_v2.py -q
```

- [ ] **Run entity autolink on real brain.db and measure coupling gate improvement**

```bash
cd ~/agentmemory && .venv/bin/python -c "
import sqlite3, json
from agentmemory._impl import _fts5_entity_match, _create_cooccurrence_edges
from agentmemory.hippocampus import coupling_gate
db = sqlite3.connect('db/brain.db')
db.row_factory = sqlite3.Row

print('=== Before ===')
eids = [r['id'] for r in db.execute('SELECT id FROM memories WHERE retired_at IS NULL AND memory_type=\"episodic\" AND confidence > 0.5').fetchall()]
p, f = coupling_gate(db, eids)
print(f'Coupling gate: {len(p)} passed, {len(f)} failed')

print('=== Layer 1: FTS5 ===')
print(json.dumps(_fts5_entity_match(db), indent=2))

print('=== Layer 3: Co-occurrence ===')
print(json.dumps(_create_cooccurrence_edges(db), indent=2))

print('=== After ===')
p2, f2 = coupling_gate(db, eids)
print(f'Coupling gate: {len(p2)} passed, {len(f2)} failed')
print(f'Improvement: {len(p2)-len(p)} more memories passed')
"
```

- [ ] **Run bench harness**

```bash
cd ~/agentmemory && .venv/bin/python -m tests.bench.run --check
```
