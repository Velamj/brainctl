"""Tests for V2-1: FTS5 Entity Name Matching (Layer 1).

Tests _fts5_entity_match and `brainctl entity autolink` CLI command.

Strategy: create memories BEFORE creating entities so the on-ingest auto-linker
(which runs during brain.remember()) cannot pre-empt the batch autolink.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _fts5_entity_match, _AUTOLINK_MIN_NAME_LENGTH
from agentmemory.brain import Brain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def raw_conn(brain: Brain) -> sqlite3.Connection:
    """Return a raw sqlite3 connection to the brain DB with row_factory set."""
    conn = sqlite3.connect(str(brain.db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def insert_memory_raw(conn: sqlite3.Connection, content: str, agent_id: str = "test-agent") -> int:
    """Insert a memory directly into the DB, bypassing the on-ingest auto-linker."""
    cur = conn.execute(
        "INSERT INTO memories (content, category, agent_id, confidence, created_at, updated_at) "
        "VALUES (?, 'project', ?, 1.0, strftime('%Y-%m-%dT%H:%M:%S','now'), "
        "strftime('%Y-%m-%dT%H:%M:%S','now'))",
        (content, agent_id),
    )
    # Also insert into FTS5 index if it exists
    mem_id = cur.lastrowid
    try:
        conn.execute("INSERT INTO memories_fts(rowid, content) VALUES (?, ?)", (mem_id, content))
    except Exception:
        pass
    conn.commit()
    return mem_id


def edge_count(conn: sqlite3.Connection, mem_id: int) -> int:
    """Count knowledge_edges for a given memory id."""
    row = conn.execute(
        "SELECT COUNT(*) FROM knowledge_edges "
        "WHERE source_table='memories' AND source_id=? "
        "AND target_table='entities' AND relation_type='mentions'",
        (mem_id,),
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAutolink:

    def test_exact_name_match_creates_edge(self, brain):
        """An entity name that appears in a memory content gets an edge."""
        conn = raw_conn(brain)

        # Insert memory BEFORE creating entity to bypass on-ingest auto-linker.
        mem_id = insert_memory_raw(conn, "We deployed Kokoro to production.")

        # Now create the entity.
        brain.entity("Kokoro", "agent", observations=["Terminal console for OpenClaw"])

        stats = _fts5_entity_match(conn)
        assert stats["edges_created"] >= 1
        assert edge_count(conn, mem_id) == 1
        conn.close()

    def test_case_insensitive_matching(self, brain):
        """Matching is case-insensitive: 'kokoro' matches entity 'Kokoro'."""
        conn = raw_conn(brain)

        mem_id = insert_memory_raw(conn, "KOKORO rocks the terminal.")
        brain.entity("Kokoro", "agent")

        stats = _fts5_entity_match(conn)
        assert stats["edges_created"] >= 1
        assert edge_count(conn, mem_id) == 1
        conn.close()

    def test_no_match_no_edge(self, brain):
        """Memory with no entity name mention gets no edge."""
        conn = raw_conn(brain)

        mem_id = insert_memory_raw(conn, "This memory mentions nothing known.")
        brain.entity("CostClock", "project")

        stats = _fts5_entity_match(conn)
        assert edge_count(conn, mem_id) == 0
        conn.close()

    def test_multiple_entities_in_one_memory(self, brain):
        """Multiple entity names in one memory creates multiple edges."""
        conn = raw_conn(brain)

        mem_id = insert_memory_raw(conn, "OpenClaw uses brainctl for memory storage.")
        brain.entity("OpenClaw", "agent")
        brain.entity("brainctl", "tool")

        stats = _fts5_entity_match(conn)
        assert edge_count(conn, mem_id) == 2
        assert stats["edges_created"] >= 2
        conn.close()

    def test_idempotent_no_duplicates(self, brain):
        """Running autolink twice creates no duplicate edges."""
        conn = raw_conn(brain)

        mem_id = insert_memory_raw(conn, "We deployed Kokoro again.")
        brain.entity("Kokoro", "agent")

        stats1 = _fts5_entity_match(conn)
        assert stats1["edges_created"] >= 1

        # Second run: memory is already linked, so skipped.
        stats2 = _fts5_entity_match(conn)
        assert stats2["edges_created"] == 0
        assert stats2["skipped_already_linked"] >= 1

        # Still exactly one edge.
        assert edge_count(conn, mem_id) == 1
        conn.close()

    def test_short_entity_names_skipped(self, brain):
        """Entity names shorter than _AUTOLINK_MIN_NAME_LENGTH are not matched."""
        assert _AUTOLINK_MIN_NAME_LENGTH == 3  # sanity check on the constant

        conn = raw_conn(brain)
        mem_id = insert_memory_raw(conn, "The AI entity is active.")

        # 'AI' is 2 chars — must be skipped.
        # Ensure the agent FK is satisfied before inserting the entity.
        conn.execute(
            "INSERT OR IGNORE INTO agents (id, display_name, agent_type, status, "
            "created_at, updated_at) VALUES ('test', 'test', 'test', 'active', "
            "strftime('%Y-%m-%dT%H:%M:%S','now'), strftime('%Y-%m-%dT%H:%M:%S','now'))"
        )
        conn.execute(
            "INSERT INTO entities (name, entity_type, properties, observations, agent_id, "
            "created_at, updated_at) VALUES ('AI', 'concept', '{}', '[]', 'test', "
            "strftime('%Y-%m-%dT%H:%M:%S','now'), strftime('%Y-%m-%dT%H:%M:%S','now'))"
        )
        conn.commit()

        stats = _fts5_entity_match(conn)
        assert edge_count(conn, mem_id) == 0
        conn.close()

    def test_already_linked_memories_skipped(self, brain):
        """Memories already linked via any 'mentions' edge are skipped by the scanner."""
        conn = raw_conn(brain)

        mem_id = insert_memory_raw(conn, "Kokoro terminal is fast.")
        brain.entity("Kokoro", "agent")

        # First run links it.
        stats1 = _fts5_entity_match(conn)
        assert stats1["linked"] == 1
        assert stats1["skipped_already_linked"] == 0

        # Second run: the memory is now already linked, so it should be skipped.
        insert_memory_raw(conn, "Another memory with no entities.")
        stats2 = _fts5_entity_match(conn)
        assert stats2["skipped_already_linked"] >= 1
        assert mem_id not in {
            r["source_id"]
            for r in conn.execute(
                "SELECT DISTINCT source_id FROM knowledge_edges "
                "WHERE source_table='memories' AND source_id NOT IN (SELECT id FROM memories WHERE retired_at IS NULL)"
            ).fetchall()
        }
        conn.close()


class TestAutolinkStats:
    """Verify the stats dict structure returned by _fts5_entity_match."""

    def test_stats_keys_present(self, brain):
        conn = raw_conn(brain)
        stats = _fts5_entity_match(conn)
        for key in ("linked", "edges_created", "skipped_already_linked", "memories_scanned"):
            assert key in stats, f"Missing stats key: {key}"
        conn.close()

    def test_stats_all_zero_on_empty_db(self, brain):
        """An empty DB returns all-zero stats."""
        conn = raw_conn(brain)
        stats = _fts5_entity_match(conn)
        assert stats["linked"] == 0
        assert stats["edges_created"] == 0
        assert stats["skipped_already_linked"] == 0
        assert stats["memories_scanned"] == 0
        conn.close()


class TestAutolinkCLI:
    """Integration test: `brainctl entity autolink` CLI command."""

    def test_cli_autolink_returns_json(self, cli_db):
        """The CLI command returns valid JSON with expected keys."""
        result = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(SRC)!r}); "
             f"import agentmemory._impl as _i; from pathlib import Path; "
             f"_i.DB_PATH = Path({str(cli_db)!r}); "
             f"sys.argv = ['brainctl', 'entity', 'autolink']; "
             f"_i.main()"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(SRC)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert "edges_created" in data
        assert "linked" in data

    def test_cli_autolink_layer_flag(self, cli_db):
        """The --layer flag is accepted without error."""
        result = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(SRC)!r}); "
             f"import agentmemory._impl as _i; from pathlib import Path; "
             f"_i.DB_PATH = Path({str(cli_db)!r}); "
             f"sys.argv = ['brainctl', 'entity', 'autolink', '--layer', 'fts5']; "
             f"_i.main()"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(SRC)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["layer"] == "fts5"
