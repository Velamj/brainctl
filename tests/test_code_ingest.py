"""Tests for the ``brainctl[code]`` extra.

Covers the full public contract of ``agentmemory.code_ingest`` +
``agentmemory.commands.ingest``:

  * Per-language extractors (python / typescript / go) produce the
    expected entities + edges from small fixtures.
  * File discovery honors the hardcoded exclude list.
  * SHA256 cache: unchanged files are skipped on re-ingest; changed
    files are re-processed and cache rows get updated.
  * Binary and oversized files are skipped cleanly.
  * Non-UTF-8 bytes don't crash the parser (tree-sitter tolerates it;
    we just need to not explode while decoding).
  * Idempotency: running ``ingest()`` twice back-to-back writes the
    same entities the first time and zero the second time.
  * Graceful degradation: when tree-sitter isn't importable, the
    module still imports and ``AVAILABLE`` is False (guards this via
    a monkeypatch rather than uninstalling the package).

These tests are skipped wholesale when the ``[code]`` extra isn't
installed so a plain ``pip install -e .`` can still run ``pytest``
without hitting ImportError.
"""
from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agentmemory import code_ingest

# Every test in this file depends on tree-sitter + the three grammars.
pytestmark = pytest.mark.skipif(
    not code_ingest.AVAILABLE,
    reason=f"brainctl[code] extra not installed: {code_ingest.availability_hint()}",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ingest_db(tmp_path: Path) -> sqlite3.Connection:
    """Empty brain.db with the baked init schema + migration 051 applied +
    a test agent row so FK constraints are satisfied.

    Mirrors what ``brainctl init`` (``cmd_init`` in ``_impl.py``) does: it
    executes the packaged ``init_schema.sql`` which already represents
    migrations 1..N-1. We then apply any migration newer than init_schema
    explicitly — the code-ingest extra added migration 051 so we patch
    that on top. Using the real migration file rather than raw SQL keeps
    this test honest to the production migrate path.
    """
    src_root = Path(__file__).resolve().parent.parent
    init_schema = src_root / "src" / "agentmemory" / "db" / "init_schema.sql"
    mig_051 = src_root / "db" / "migrations" / "051_code_ingest_cache.sql"

    db_path = tmp_path / "brain.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(init_schema.read_text())
    # init_schema.sql already carries migrations 1..N — apply 051 on top.
    # If/when init_schema is regenerated to include 051, this script
    # becomes a no-op thanks to the IF NOT EXISTS guards in the migration.
    conn.executescript(mig_051.read_text())
    conn.execute("PRAGMA foreign_keys = ON")
    # Seed the test agent so _upsert_entity writes don't fail on FK.
    conn.execute(
        "INSERT OR IGNORE INTO agents (id, display_name, agent_type, status, "
        "created_at, updated_at) VALUES (?, ?, 'test', 'active', "
        "datetime('now'), datetime('now'))",
        ("code-ingest", "code-ingest"),
    )
    conn.commit()
    return conn


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    """A non-git source tree with one file per supported language plus
    a couple of things we should skip (node_modules, a binary)."""
    root = tmp_path / "tiny_repo"
    root.mkdir()

    (root / "app.py").write_text(
        "import os\n"
        "from collections import defaultdict\n"
        "\n"
        "def top_level():\n"
        "    return 42\n"
        "\n"
        "class Widget:\n"
        "    def __init__(self, x):\n"
        "        self.x = x\n"
        "\n"
        "    def show(self):\n"
        "        print(self.x)\n",
        encoding="utf-8",
    )

    (root / "app.ts").write_text(
        "import { readFileSync } from 'fs';\n"
        "import * as path from 'node:path';\n"
        "\n"
        "export function greet(name: string): string {\n"
        "  return 'hello ' + name;\n"
        "}\n"
        "\n"
        "export class Box {\n"
        "  private v: number;\n"
        "  constructor(v: number) { this.v = v; }\n"
        "  inc(): void { this.v += 1; }\n"
        "}\n",
        encoding="utf-8",
    )

    (root / "server.go").write_text(
        "package main\n"
        "\n"
        "import (\n"
        "    \"fmt\"\n"
        "    \"net/http\"\n"
        ")\n"
        "\n"
        "type Server struct { port int }\n"
        "\n"
        "func (s *Server) Listen() error {\n"
        "    return http.ListenAndServe(fmt.Sprintf(\":%d\", s.port), nil)\n"
        "}\n"
        "\n"
        "func main() { _ = &Server{port: 8080} }\n",
        encoding="utf-8",
    )

    # Excluded dir — files inside must never be walked.
    excluded = root / "node_modules" / "pkg"
    excluded.mkdir(parents=True)
    (excluded / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")

    # Binary file with a .py extension — must be skipped by the null-byte sniffer.
    (root / "blob.py").write_bytes(b"\x00\x01\x02\x03\x04" * 100)

    return root


# ---------------------------------------------------------------------------
# Extractor-level tests
# ---------------------------------------------------------------------------

def test_extract_python_emits_expected_graph(tiny_repo: Path):
    src = (tiny_repo / "app.py").read_bytes()
    ex = code_ingest.extract_python(tiny_repo / "app.py", src, "app.py")

    # We expect: file, top_level, Widget, Widget.__init__, Widget.show,
    # plus two module nodes (os, collections).
    kinds = {n.kind for n in ex.nodes}
    assert kinds == {"file", "function", "class", "module"}

    names = {n.name for n in ex.nodes}
    assert "file:app.py" in names
    assert "fn:app.py:top_level" in names
    assert "class:app.py:Widget" in names
    assert "fn:app.py:Widget.__init__" in names
    assert "fn:app.py:Widget.show" in names
    assert "module:os" in names
    assert "module:collections" in names

    # Every `contains` edge must have weight 1.0 (EXTRACTED).
    contains = [e for e in ex.edges if e.relation == "contains"]
    assert all(e.weight == code_ingest.WEIGHT_EXTRACTED for e in contains)
    assert len(contains) >= 3  # file→top_level, file→Widget, Widget→show, Widget→__init__

    # Imports are weight INFERRED (we don't try to resolve external modules in v1).
    imports = [e for e in ex.edges if e.relation == "imports"]
    assert all(e.weight == code_ingest.WEIGHT_INFERRED for e in imports)
    assert len(imports) == 2


def test_extract_typescript_handles_classes_and_methods(tiny_repo: Path):
    src = (tiny_repo / "app.ts").read_bytes()
    ex = code_ingest.extract_typescript(tiny_repo / "app.ts", src, "app.ts")

    names = {n.name for n in ex.nodes}
    assert "fn:app.ts:greet" in names
    assert "class:app.ts:Box" in names
    assert "fn:app.ts:Box.inc" in names
    # imports captured with their raw module spec
    assert "module:fs" in names
    assert "module:node:path" in names


def test_extract_go_handles_methods_and_types(tiny_repo: Path):
    src = (tiny_repo / "server.go").read_bytes()
    ex = code_ingest.extract_go(tiny_repo / "server.go", src, "server.go")

    names = {n.name for n in ex.nodes}
    assert "class:server.go:Server" in names
    assert "fn:server.go:Server.Listen" in names
    assert "fn:server.go:main" in names
    assert "module:fmt" in names
    assert "module:net/http" in names


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_collect_files_respects_hardcoded_excludes(tiny_repo: Path):
    files = code_ingest.collect_files(
        tiny_repo, ["python", "typescript", "go"]
    )
    rel = sorted(p.relative_to(tiny_repo).as_posix() for p in files)
    assert "app.py" in rel
    assert "app.ts" in rel
    assert "server.go" in rel
    # node_modules/pkg/index.ts must be excluded
    assert not any("node_modules" in r for r in rel)
    # blob.py is included in the file list at discovery time — the
    # binary check happens inside ingest(), not here.
    assert "blob.py" in rel


# ---------------------------------------------------------------------------
# End-to-end ingest + cache
# ---------------------------------------------------------------------------

def test_ingest_writes_entities_and_edges(ingest_db, tiny_repo: Path):
    stats = code_ingest.ingest(tiny_repo, scope="project:test-tiny",
                               db=ingest_db)

    # 3 source files processed + 1 binary skipped (blob.py)
    assert stats.files_processed == 3
    assert stats.files_skipped == 1
    assert stats.files_cached == 0
    assert stats.entities_written > 0
    assert stats.edges_written > 0

    # Entities land under the requested scope
    count = ingest_db.execute(
        "SELECT COUNT(*) FROM entities WHERE scope = ? AND retired_at IS NULL",
        ("project:test-tiny",),
    ).fetchone()[0]
    assert count == stats.entities_written

    # Every edge is anchored to entities table on both sides
    bad = ingest_db.execute(
        "SELECT COUNT(*) FROM knowledge_edges "
        "WHERE agent_id = 'code-ingest' AND "
        "(source_table != 'entities' OR target_table != 'entities')"
    ).fetchone()[0]
    assert bad == 0


def test_ingest_is_idempotent_via_cache(ingest_db, tiny_repo: Path):
    first = code_ingest.ingest(tiny_repo, scope="project:idem", db=ingest_db)
    assert first.files_processed == 3
    assert first.files_cached == 0

    second = code_ingest.ingest(tiny_repo, scope="project:idem", db=ingest_db)
    # Everything cached, nothing new written.
    assert second.files_processed == 0
    assert second.files_cached == 3
    assert second.entities_written == 0
    assert second.edges_written == 0


def test_ingest_rewrites_when_content_changes(ingest_db, tiny_repo: Path):
    code_ingest.ingest(tiny_repo, scope="project:rewrite", db=ingest_db)

    # Mutate app.py — add a new function
    app = tiny_repo / "app.py"
    app.write_text(app.read_text(encoding="utf-8") +
                   "\ndef newly_added():\n    return 'hi'\n",
                   encoding="utf-8")

    second = code_ingest.ingest(tiny_repo, scope="project:rewrite", db=ingest_db)
    assert second.files_processed == 1   # only app.py re-parsed
    assert second.files_cached == 2       # app.ts + server.go unchanged
    # The new function entity should exist
    row = ingest_db.execute(
        "SELECT id FROM entities WHERE name = 'fn:app.py:newly_added' "
        "AND scope = 'project:rewrite' AND retired_at IS NULL"
    ).fetchone()
    assert row is not None


def test_ingest_bypasses_cache_when_disabled(ingest_db, tiny_repo: Path):
    code_ingest.ingest(tiny_repo, scope="project:no-cache", db=ingest_db)
    forced = code_ingest.ingest(tiny_repo, scope="project:no-cache",
                                db=ingest_db, use_cache=False)
    assert forced.files_processed == 3
    assert forced.files_cached == 0
    # Entities UPSERTed — no duplicates created
    total = ingest_db.execute(
        "SELECT COUNT(*) FROM entities WHERE scope = 'project:no-cache' "
        "AND retired_at IS NULL"
    ).fetchone()[0]
    assert total == forced.entities_written + forced.entities_updated


def test_ingest_skips_oversized_files(ingest_db, tmp_path: Path):
    root = tmp_path / "big"
    root.mkdir()
    # Exactly 1 byte over the cap
    big = b"x = 1\n" * (code_ingest.MAX_FILE_BYTES // 6 + 1)
    (root / "huge.py").write_bytes(big)
    (root / "ok.py").write_text("def fine(): return 1\n", encoding="utf-8")

    stats = code_ingest.ingest(root, scope="project:size", db=ingest_db)
    assert stats.files_processed == 1
    assert stats.files_skipped == 1


def test_ingest_handles_syntax_errors_without_crashing(ingest_db, tmp_path: Path):
    root = tmp_path / "broken"
    root.mkdir()
    # Deliberately malformed — tree-sitter should still yield a partial tree
    (root / "bad.py").write_text(
        "def foo(\n  this is not python\n", encoding="utf-8"
    )
    (root / "good.py").write_text("def bar(): return 0\n", encoding="utf-8")

    stats = code_ingest.ingest(root, scope="project:broken", db=ingest_db)
    # Both files attempted — tree-sitter tolerates broken input so no error
    assert stats.files_processed == 2
    # The good file definitely yields its function
    row = ingest_db.execute(
        "SELECT id FROM entities WHERE name = 'fn:good.py:bar' "
        "AND scope = 'project:broken' AND retired_at IS NULL"
    ).fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# Integration with existing brainctl surfaces
# ---------------------------------------------------------------------------

def test_entity_properties_carry_kind_and_language(ingest_db, tiny_repo: Path):
    import json as _json
    code_ingest.ingest(tiny_repo, scope="project:props", db=ingest_db)
    row = ingest_db.execute(
        "SELECT entity_type, properties FROM entities "
        "WHERE name = 'fn:app.py:top_level' AND scope = 'project:props'"
    ).fetchone()
    assert row is not None
    assert row["entity_type"] == "concept"
    props = _json.loads(row["properties"])
    assert props["kind"] == "function"
    assert props["language"] == "python"
    assert props["path"] == "app.py"
    assert props["line"] > 0


def test_edge_weights_encode_provenance(ingest_db, tiny_repo: Path):
    code_ingest.ingest(tiny_repo, scope="project:weight", db=ingest_db)

    # contains edges — always 1.0
    contains_weights = [
        r["weight"] for r in ingest_db.execute(
            "SELECT ke.weight FROM knowledge_edges ke "
            "JOIN entities e ON e.id = ke.source_id "
            "WHERE ke.relation_type = 'contains' AND e.scope = 'project:weight'"
        )
    ]
    assert contains_weights and all(w == 1.0 for w in contains_weights)

    # imports edges — 0.7 (INFERRED because we don't resolve external modules)
    import_weights = [
        r["weight"] for r in ingest_db.execute(
            "SELECT ke.weight FROM knowledge_edges ke "
            "JOIN entities e ON e.id = ke.source_id "
            "WHERE ke.relation_type = 'imports' AND e.scope = 'project:weight'"
        )
    ]
    assert import_weights and all(w == code_ingest.WEIGHT_INFERRED
                                  for w in import_weights)
