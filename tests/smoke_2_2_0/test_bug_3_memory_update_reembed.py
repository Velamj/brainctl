"""Smoke test for 2.2.0 Bug 3: cmd_memory_update never re-embeds.

Pre-fix: cmd_memory_update wrote new content to the `memories` table but
left vec_memories pointing at the stale embedding. Semantic search after
an update returned results matching the OLD content.

Post-fix: when --content changes, the function re-embeds and writes
INSERT OR REPLACE into vec_memories. If the sqlite-vec extension is not
loaded the function degrades gracefully (stderr warning, no crash) and
returns ok=True with reembedded=False.

We exercise the function by monkey-patching its module-level helpers
(get_db, _embed_query_safe, _try_get_db_with_vec, log_access, json_out)
to capture the SQL statements and the JSON output, without needing a
real brain.db or a working ollama embed server.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agentmemory._impl as impl  # noqa: E402


def _make_memories_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            category TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'global',
            content TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            tags TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT,
            retired_at TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO memories (id, agent_id, category, content, version) "
        "VALUES (1, 'a', 'lesson', 'old text', 1)"
    )
    db.commit()
    return db


class _NonClosingConn:
    """sqlite3 connection wrapper that ignores close() so tests can inspect
    state after the system-under-test thinks it's done with the connection.
    The real cmd_memory_update closes the vec-loaded handle in a finally
    block; for our in-memory test DB closing it would discard our data.
    """

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):  # explicit no-op
        pass

    def really_close(self):
        self._conn.close()


def _make_vec_db():
    """Stand-in for the vec-loaded DB. Just needs to accept the two writes."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE vec_memories (rowid INTEGER PRIMARY KEY, embedding BLOB);
        CREATE TABLE embeddings (
            source_table TEXT, source_id INTEGER, model TEXT,
            dimensions INTEGER, vector BLOB,
            UNIQUE(source_table, source_id)
        );
        """
    )
    return _NonClosingConn(db)


@pytest.fixture
def patched(monkeypatch):
    """Patch impl module helpers and capture json_out result."""
    main_db = _make_memories_db()
    vec_db = _make_vec_db()
    captured = {}

    monkeypatch.setattr(impl, "get_db", lambda: main_db)
    monkeypatch.setattr(impl, "log_access", lambda *a, **kw: None)
    monkeypatch.setattr(impl, "json_out", lambda d: captured.setdefault("out", d))
    monkeypatch.setattr(impl, "_embed_query_safe", lambda text: b"\x01\x02\x03")
    monkeypatch.setattr(impl, "_try_get_db_with_vec", lambda: vec_db)

    yield SimpleNamespace(main_db=main_db, vec_db=vec_db, captured=captured)


def _args(**kw):
    base = dict(
        id=1,
        expected_version=1,
        agent="a",
        content=None,
        confidence=None,
        tags=None,
        scope=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestBug3ReEmbed:
    def test_content_change_writes_vec_memories(self, patched):
        impl.cmd_memory_update(_args(content="brand new text"))
        out = patched.captured["out"]
        assert out["ok"] is True
        assert out["reembedded"] is True
        # vec_memories row was actually written with the new embedding blob
        row = patched.vec_db.execute(
            "SELECT rowid, embedding FROM vec_memories WHERE rowid=1"
        ).fetchone()
        assert row is not None
        assert bytes(row["embedding"]) == b"\x01\x02\x03"

    def test_content_change_writes_embeddings_audit(self, patched):
        impl.cmd_memory_update(_args(content="audit me"))
        rows = patched.vec_db.execute(
            "SELECT source_table, source_id, model, dimensions FROM embeddings"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["source_table"] == "memories"
        assert rows[0]["source_id"] == 1
        assert rows[0]["model"] == impl.EMBED_MODEL
        assert rows[0]["dimensions"] == impl.EMBED_DIMENSIONS

    def test_no_content_change_skips_reembed(self, patched):
        # Confidence-only change should NOT touch vec_memories.
        impl.cmd_memory_update(_args(confidence=0.7))
        out = patched.captured["out"]
        assert out["ok"] is True
        assert out["reembedded"] is False
        rows = patched.vec_db.execute(
            "SELECT COUNT(*) AS c FROM vec_memories"
        ).fetchone()
        assert rows["c"] == 0

    def test_vec_extension_missing_warns_no_crash(self, patched, capsys, monkeypatch):
        monkeypatch.setattr(impl, "_try_get_db_with_vec", lambda: None)
        impl.cmd_memory_update(_args(content="content with no vec ext"))
        out = patched.captured["out"]
        # Update still succeeds; reembedded flag is False.
        assert out["ok"] is True
        assert out["reembedded"] is False
        captured = capsys.readouterr()
        assert "vec extension not loaded" in captured.err
        assert "memory 1" in captured.err

    def test_version_conflict_skips_reembed(self, patched):
        # expected_version mismatch → CAS fails → must NOT touch vec_memories.
        impl.cmd_memory_update(_args(content="should not embed", expected_version=99))
        out = patched.captured["out"]
        assert out["ok"] is False
        assert out["error"] == "version_conflict"
        rows = patched.vec_db.execute(
            "SELECT COUNT(*) AS c FROM vec_memories"
        ).fetchone()
        assert rows["c"] == 0

    def test_embed_failure_is_nonfatal(self, patched, capsys, monkeypatch):
        # Simulate ollama returning None (no embedding available).
        monkeypatch.setattr(impl, "_embed_query_safe", lambda text: None)
        impl.cmd_memory_update(_args(content="cannot embed"))
        out = patched.captured["out"]
        assert out["ok"] is True
        # No blob → no vec write, but no crash.
        assert out["reembedded"] is False
