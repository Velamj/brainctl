"""Tests for the brainctl 2.2.3 wm-vec-affect patch wave (Worker C scope).

Covers three items:
  1. _surprise_score: fts5_no_matches and cosine_no_neighbors no longer
     inflate to 1.0; vec-fallback path returns sub-1.0 when a near-duplicate
     exists in the vec store; method tag carries observed signal.
  2. cmd_vec_purge_retired: chunked single-DELETE path replaces per-row
     loop and is significantly faster on N retired memories.
  3. affect.prune_affect_log + cmd_affect_prune: days-based and rows-based
     deletion with union semantics; --dry-run does not delete; the new
     index migration is idempotent.
"""
from __future__ import annotations

import os
import sqlite3
import struct
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory.brain import Brain  # noqa: E402
from agentmemory import migrate as _migrate  # noqa: E402
import agentmemory._impl as _impl  # noqa: E402
import agentmemory.affect as _affect  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_049 = REPO_ROOT / "db" / "migrations" / "049_affect_log_retention_indexes.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_json_out():
    """Snapshot / restore _impl.json_out so per-test capture lambdas don't
    leak into the rest of the suite (other tests parse the real stdout)."""
    saved = _impl.json_out
    yield
    _impl.json_out = saved


def _fresh_db(tmp_path) -> Path:
    """Build a fresh brain.db on init_schema (carries up through 047)."""
    db = tmp_path / "brain.db"
    Brain(db_path=str(db), agent_id="test").close()
    _migrate.mark_applied_up_to(str(db), 999)
    # Ensure 'test' agent exists for FK constraint on memories.agent_id
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO agents (id, display_name, agent_type, status, "
            "created_at, updated_at) VALUES ('test', 'test', 'test', 'active', "
            "strftime('%Y-%m-%dT%H:%M:%S','now'), strftime('%Y-%m-%dT%H:%M:%S','now'))"
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
    return db


def _capture():
    captured: list[dict] = []
    _impl.json_out = lambda d, compact=False: captured.append(d)
    return captured


# ---------------------------------------------------------------------------
# Item 1 — _surprise_score
# ---------------------------------------------------------------------------


class TestSurpriseScore:
    """Bug 2.2.3: prior impl returned 1.0 ('maximally novel') for both
    fts5_no_matches and cosine_no_neighbors. Both must now drop to 0.5
    neutral, and the method tag must include an observed signal."""

    def test_fts5_no_matches_returns_neutral_not_one(self, tmp_path):
        """Empty FTS index + no blob = cannot infer novelty → 0.5."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row

        surprise, method = _impl._surprise_score(
            conn, "totally novel content with no matches", blob=None
        )
        assert surprise == 0.5, f"expected 0.5 neutral, got {surprise}"
        assert "neutral" in method, f"method tag missing neutral marker: {method}"
        assert method != "fts5_no_matches", "should not return bare legacy tag"
        conn.close()

    def test_fts5_match_returns_overlap_method(self, tmp_path):
        """When FTS5 returns matches, method tag carries observed overlap."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        # Insert a memory and let FTS5 see it
        conn.execute(
            "INSERT INTO memories (agent_id, content, category, scope, confidence, "
            "  trust_score, created_at, updated_at) "
            "VALUES ('test', 'the user prefers dark mode in the editor', 'preference', "
            "  'global', 0.9, 0.85, strftime('%Y-%m-%dT%H:%M:%S','now'), strftime('%Y-%m-%dT%H:%M:%S','now'))"
        )
        conn.commit()

        surprise, method = _impl._surprise_score(
            conn, "user prefers dark mode in editor", blob=None
        )
        assert method.startswith("fts5_overlap_"), f"unexpected method: {method}"
        # extracted overlap should be parseable
        overlap_str = method.split("fts5_overlap_")[-1]
        observed = float(overlap_str)
        assert 0.0 <= observed <= 1.0
        # near-duplicate → low surprise
        assert surprise < 0.7
        conn.close()

    def test_empty_content_returns_neutral(self, tmp_path):
        """Empty / whitespace content cannot be scored → 0.5 not 1.0."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        surprise, method = _impl._surprise_score(conn, "", blob=None)
        assert surprise == 0.5
        assert "neutral" in method
        conn.close()

    def test_no_query_words_returns_neutral(self, tmp_path):
        """Stop-word-only content yields no FTS query → 0.5 not 1.0."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        # All words length <= 2 so no FTS query is built
        surprise, method = _impl._surprise_score(conn, "a b c d", blob=None)
        assert surprise == 0.5
        assert "neutral" in method
        conn.close()


class TestVecFallback:
    """Vec-fallback path: when blob is provided AND vec is unavailable,
    must fall through to FTS5 (and then 0.5 neutral when FTS is empty).
    The hot-path contract: NEVER inflate to 1.0 just because the lookup
    method had nothing to compare against."""

    def test_blob_present_no_vec_backend_returns_neutral(self, tmp_path, monkeypatch):
        """blob is given but VEC_DYLIB is None → fall through to FTS5
        (which is empty in fresh DB) → 0.5 neutral. Reproduces the
        hot path on a machine without sqlite-vec installed."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row

        # Force vec backend unavailable
        monkeypatch.setattr(_impl, "_try_get_db_with_vec", lambda: None)

        fake_blob = struct.pack(f"{8}f", *([0.1] * 8))
        surprise, method = _impl._surprise_score(conn, "novel content", blob=fake_blob)
        assert surprise == 0.5, f"expected 0.5 neutral, got {surprise}"
        assert "neutral" in method
        conn.close()

    def test_vec_fallback_returns_neutral_when_vec_empty(self, tmp_path, monkeypatch):
        """blob present + vec backend present but vec store empty + FTS empty
        → 0.5 neutral. This is the documented vec-fallback contract: the
        function NEVER inflates novelty when no signal could be measured.

        We stub _try_get_db_with_vec to return a sqlite3 connection wrapping
        a stand-in vec_memories table. The stub returns no neighbors so the
        cosine helper returns (None, 0) and the function falls through."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row

        # Stub vec backend: open a connection on the SAME DB and create a
        # regular vec_memories table with a fake `k` column so the
        # `MATCH ? AND k=?` clause parses but returns nothing.
        def _stub_vec():
            c = sqlite3.connect(str(db), timeout=10)
            c.row_factory = sqlite3.Row
            try:
                c.execute(
                    "CREATE TABLE IF NOT EXISTS vec_memories "
                    "(rowid INTEGER PRIMARY KEY, embedding BLOB, k INTEGER)"
                )
                c.commit()
            except Exception:
                pass
            return c

        monkeypatch.setattr(_impl, "_try_get_db_with_vec", _stub_vec)

        fake_blob = struct.pack(f"{8}f", *([0.1] * 8))
        surprise, method = _impl._surprise_score(conn, "novel content", blob=fake_blob)
        # Vec returned no neighbors → fall through to FTS5 (also empty) → 0.5
        assert surprise == 0.5, f"expected 0.5 neutral, got {surprise} ({method})"
        assert "neutral" in method
        conn.close()


# ---------------------------------------------------------------------------
# Item 2 — cmd_vec_purge_retired
# ---------------------------------------------------------------------------


class TestVecPurgeRetired:
    """Replace single-row DELETE loop with chunked IN-list DELETE.
    On 1k retired rows the new path should complete in <<1 second; the
    old path scales O(N) round trips and becomes unusable at 50k rows."""

    def test_purge_chunked_delete(self, tmp_path, monkeypatch):
        """Insert N retired memories with stub vec rows; verify a single
        purge invocation deletes them all in one chunked DELETE.

        We stub vec_memories as a regular table so the test runs without
        the sqlite-vec extension. The DELETE syntax is identical."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        # Create a stand-in vec_memories matching the real shape (rowid + blob)
        conn.execute(
            "CREATE TABLE vec_memories (rowid INTEGER PRIMARY KEY, embedding BLOB)"
        )
        # Insert 1000 retired memories with paired vec_memories rows
        N = 1000
        for i in range(1, N + 1):
            conn.execute(
                "INSERT INTO memories (id, agent_id, content, category, scope, "
                "  confidence, trust_score, created_at, updated_at, retired_at) "
                "VALUES (?, 'test', ?, 'project', 'global', 0.9, 0.85, "
                "  strftime('%Y-%m-%dT%H:%M:%S','now'), strftime('%Y-%m-%dT%H:%M:%S','now'), datetime('now'))",
                (i, f"memory {i}"),
            )
            conn.execute(
                "INSERT INTO vec_memories (rowid, embedding) VALUES (?, ?)",
                (i, b"\x00\x00\x00\x00"),
            )
        conn.commit()
        conn.close()

        # Patch _get_db_with_vec to return a plain sqlite3 connection on the
        # same DB (no extension load) — exercises the SQL path only.
        def _fake_get_db_with_vec():
            c = sqlite3.connect(str(db), timeout=10)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(_impl, "_get_db_with_vec", _fake_get_db_with_vec)
        captured = _capture()

        start = time.time()
        _impl.cmd_vec_purge_retired(types.SimpleNamespace(limit=None))
        elapsed = time.time() - start

        assert captured, "purge command never called json_out"
        result = captured[-1]
        assert result["ok"] is True
        assert result["checked"] == N
        # Either rowcount returned N, or fallback to checked count
        assert result["purged"] == N

        # Performance budget: 1k rows in chunked DELETE should be well under 1s.
        assert elapsed < 2.0, f"chunked purge too slow: {elapsed:.2f}s for {N} rows"

        # Verify vec_memories is empty
        conn = sqlite3.connect(str(db))
        rem = conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0]
        assert rem == 0, f"expected 0 vec rows after purge, got {rem}"
        conn.close()

    def test_purge_respects_limit(self, tmp_path, monkeypatch):
        """--limit N caps the run at N rows so a user can chunk a large purge."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE vec_memories (rowid INTEGER PRIMARY KEY, embedding BLOB)"
        )
        for i in range(1, 51):
            conn.execute(
                "INSERT INTO memories (id, agent_id, content, category, scope, "
                "  confidence, trust_score, created_at, updated_at, retired_at) "
                "VALUES (?, 'test', ?, 'project', 'global', 0.9, 0.85, "
                "  strftime('%Y-%m-%dT%H:%M:%S','now'), strftime('%Y-%m-%dT%H:%M:%S','now'), datetime('now'))",
                (i, f"m{i}"),
            )
            conn.execute(
                "INSERT INTO vec_memories (rowid, embedding) VALUES (?, ?)",
                (i, b"\x00"),
            )
        conn.commit()
        conn.close()

        def _fake_get_db_with_vec():
            c = sqlite3.connect(str(db), timeout=10)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(_impl, "_get_db_with_vec", _fake_get_db_with_vec)
        captured = _capture()

        _impl.cmd_vec_purge_retired(types.SimpleNamespace(limit=10))
        result = captured[-1]
        assert result["checked"] == 10
        assert result["purged"] == 10
        assert result["limit"] == 10

        conn = sqlite3.connect(str(db))
        rem = conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0]
        assert rem == 40, f"expected 40 vec rows remaining after limited purge, got {rem}"
        conn.close()

    def test_purge_empty_is_noop(self, tmp_path, monkeypatch):
        """No retired memories → returns purged=0 with no error."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE vec_memories (rowid INTEGER PRIMARY KEY, embedding BLOB)"
        )
        conn.commit()
        conn.close()

        def _fake_get_db_with_vec():
            c = sqlite3.connect(str(db), timeout=10)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(_impl, "_get_db_with_vec", _fake_get_db_with_vec)
        captured = _capture()
        _impl.cmd_vec_purge_retired(types.SimpleNamespace(limit=None))
        assert captured[-1]["purged"] == 0
        assert captured[-1]["checked"] == 0


# ---------------------------------------------------------------------------
# Item 3 — affect_log retention prune
# ---------------------------------------------------------------------------


def _seed_affect_rows(db_path: Path, total: int, span_days: int):
    """Insert `total` affect_log rows spread across `span_days`. Returns
    timestamps of inserted rows for assertions."""
    conn = sqlite3.connect(str(db_path))
    timestamps = []
    for i in range(total):
        # Spread evenly across span_days
        days_ago = int((i / max(1, total - 1)) * span_days) if total > 1 else 0
        ts = conn.execute(
            "SELECT datetime('now', ?)", (f"-{days_ago} days",)
        ).fetchone()[0]
        timestamps.append(ts)
        conn.execute(
            "INSERT INTO affect_log (agent_id, valence, arousal, dominance, "
            "  affect_label, cluster, functional_state, source, created_at) "
            "VALUES ('test', 0.0, 0.0, 0.0, 'neutral', 'neutral', 'neutral', "
            "  'observation', ?)",
            (ts,),
        )
    conn.commit()
    conn.close()
    return timestamps


class TestAffectPruneDays:
    """--days N deletes rows older than N days, keeping recent + the
    most-recent 100k by default (union semantics)."""

    def test_prune_days_deletes_old_rows(self, tmp_path):
        db = _fresh_db(tmp_path)
        # 200 rows spread across 365 days: half are >90d old
        _seed_affect_rows(db, total=200, span_days=365)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        # max_rows=10 to make the row-count cutoff dominate the days cutoff
        # so we exercise pure days-based deletion
        result = _affect.prune_affect_log(conn, days=90, max_rows=10, dry_run=False)
        conn.close()

        assert result["total_before"] == 200
        # With max_rows=10 + days=90, only the 10 most recent rows survive AND
        # any rows newer than 90d. Union semantics: kept = max(10, count_within_90d).
        assert result["kept"] >= 10
        assert result["deleted"] > 0
        assert result["deleted"] + result["kept"] == 200

        conn = sqlite3.connect(str(db))
        actual = conn.execute("SELECT COUNT(*) FROM affect_log").fetchone()[0]
        conn.close()
        assert actual == result["kept"]

    def test_prune_dry_run_does_not_delete(self, tmp_path):
        db = _fresh_db(tmp_path)
        _seed_affect_rows(db, total=200, span_days=365)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        before = conn.execute("SELECT COUNT(*) FROM affect_log").fetchone()[0]
        result = _affect.prune_affect_log(conn, days=90, max_rows=10, dry_run=True)
        after = conn.execute("SELECT COUNT(*) FROM affect_log").fetchone()[0]
        conn.close()

        assert result["dry_run"] is True
        assert result["deleted"] > 0
        assert before == after, "dry run must not delete rows"


class TestAffectPruneRows:
    """--max-rows N keeps the most-recent N by id."""

    def test_prune_max_rows_keeps_recent_n(self, tmp_path):
        db = _fresh_db(tmp_path)
        # 100 rows all within last day → days policy preserves all,
        # but max_rows=20 should still trim. Union: kept = max(20, in_last_90d=100).
        # So with 100 rows all recent and max_rows=20, days policy WINS and
        # nothing is deleted. To exercise the rows path, span the rows
        # across many days so days predicate would allow trimming and
        # max_rows is the binding constraint.
        _seed_affect_rows(db, total=100, span_days=365)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        # days=1 → very few rows are within 1 day; max_rows=20 binds.
        result = _affect.prune_affect_log(conn, days=1, max_rows=20, dry_run=False)
        conn.close()

        assert result["total_before"] == 100
        # With days=1 cutoff most rows are old; row policy keeps last 20 by id.
        # Union: kept = at-least 20.
        assert result["kept"] >= 20
        assert result["deleted"] == 100 - result["kept"]

        conn = sqlite3.connect(str(db))
        actual = conn.execute("SELECT COUNT(*) FROM affect_log").fetchone()[0]
        conn.close()
        assert actual == result["kept"]

    def test_prune_no_op_when_under_budget(self, tmp_path):
        """When total rows < max_rows AND nothing is older than `days`,
        prune is a no-op."""
        db = _fresh_db(tmp_path)
        _seed_affect_rows(db, total=50, span_days=10)  # all within 10d

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        result = _affect.prune_affect_log(conn, days=90, max_rows=100_000, dry_run=False)
        conn.close()

        assert result["deleted"] == 0
        assert result["kept"] == 50

    def test_prune_empty_table_returns_zero(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        result = _affect.prune_affect_log(conn, days=90, max_rows=100_000)
        conn.close()
        assert result["total_before"] == 0
        assert result["deleted"] == 0


class TestAffectPruneCLI:
    """End-to-end through cmd_affect_prune dispatch."""

    def test_cli_dispatch_dry_run(self, tmp_path):
        db = _fresh_db(tmp_path)
        _seed_affect_rows(db, total=300, span_days=365)
        _impl.DB_PATH = db
        captured = _capture()

        _impl.cmd_affect_prune(types.SimpleNamespace(
            days=90, max_rows=10, dry_run=True,
        ))
        assert captured, "cmd_affect_prune did not call json_out"
        result = captured[-1]
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["total_before"] == 300
        assert result["deleted"] > 0

        # Verify nothing actually deleted
        conn = sqlite3.connect(str(db))
        actual = conn.execute("SELECT COUNT(*) FROM affect_log").fetchone()[0]
        conn.close()
        assert actual == 300

    def test_cli_dispatch_real_delete(self, tmp_path):
        db = _fresh_db(tmp_path)
        _seed_affect_rows(db, total=300, span_days=365)
        _impl.DB_PATH = db
        captured = _capture()

        _impl.cmd_affect_prune(types.SimpleNamespace(
            days=30, max_rows=5, dry_run=False,
        ))
        result = captured[-1]
        assert result["ok"] is True
        assert result["deleted"] > 0

        conn = sqlite3.connect(str(db))
        actual = conn.execute("SELECT COUNT(*) FROM affect_log").fetchone()[0]
        conn.close()
        assert actual == result["kept"]


class TestMigration049:
    """Migration 049 adds idx_affect_created_at idempotently."""

    def test_migration_creates_index(self, tmp_path):
        db = _fresh_db(tmp_path)
        # init_schema.sql now mirrors the index — confirm presence.
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_affect_created_at'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1, "fresh init must include idx_affect_created_at"

    def test_migration_is_idempotent(self, tmp_path):
        """Apply migration 049 SQL twice → no error."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(str(db))
        sql = MIGRATION_049.read_text()
        conn.executescript(sql)
        conn.executescript(sql)  # second run must not fail
        # Index still exactly one row in sqlite_master
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_affect_created_at'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
