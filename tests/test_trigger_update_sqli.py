"""Regression tests for the memory_triggers UPDATE SQL hardening.

Covers `_build_trigger_update_sql` (pure helper) and `tool_trigger_update`
(integration through get_db()/BRAIN_DB).

Background: 2.2.0 audit flagged src/agentmemory/mcp_server.py line 1383 as a
potential SQL-injection sink because the original handler built the SET clause
via `f\"... {', '.join(updates)} ...\"`. The handler signature uses fixed
kwargs so the wire surface could not actually deliver arbitrary column names,
but the f-string-over-runtime-list pattern was fragile defense-in-depth: any
future refactor toward a generic update path would have made it a real CWE-89.

The fix factors out a pure helper that intersects the incoming column-keyed
dict against a hardcoded allowlist sourced from db/init_schema.sql, then
formats the SQL fragment from the post-intersection list. This file pins that
invariant so future edits cannot regress it.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

# Same PYTHONPATH bootstrap pattern as test_mcp_integration.py
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory import mcp_server  # noqa: E402


# ---------------------------------------------------------------------------
# Pure helper tests — exercise the SQL-shape invariant directly
# ---------------------------------------------------------------------------

ALLOWED = mcp_server._TRIGGER_UPDATE_ALLOWED_COLUMNS


def test_build_sql_rejects_malicious_key_keeps_valid():
    """A caller (or confused upstream) passing a non-allowlisted key must
    have it dropped from both the SQL fragment and the bound params, while
    valid keys still produce a parameterized statement."""
    incoming = {
        "trigger_condition": "weather is rainy",
        # The classic CWE-89 attempt: column name carries SQL syntax.
        "1=1; DROP TABLE memories; --": "ignored",
        # Less dramatic but equally important: an unknown-but-syntactically-clean
        # column must also be filtered (prevents writing to columns the schema
        # doesn't expose, e.g. a future audit_owner column).
        "unknown_column": "also ignored",
    }
    sql, params, accepted, rejected = mcp_server._build_trigger_update_sql(incoming)

    assert sql is not None
    assert "DROP" not in sql.upper().replace("DROPPED", "")
    assert "--" not in sql
    assert ";" not in sql.rstrip(";")  # no inline statement terminators
    assert "trigger_condition = ?" in sql
    assert "unknown_column" not in sql
    assert accepted == ["trigger_condition"]
    assert params == ["weather is rainy"]
    assert set(rejected) == {"1=1; DROP TABLE memories; --", "unknown_column"}


def test_build_sql_valid_keys_produce_expected_parameterized_shape():
    """Happy-path: every allowed key gets a `col = ?` clause, in caller-given
    order, and params line up positionally."""
    incoming = {
        "trigger_condition": "cond",
        "trigger_keywords": "k1,k2",
        "action": "ping",
        "priority": "high",
        "status": "active",
        "expires_at": "2026-01-01T00:00:00",
    }
    sql, params, accepted, rejected = mcp_server._build_trigger_update_sql(incoming)

    assert rejected == []
    assert accepted == [
        "trigger_condition", "trigger_keywords", "action",
        "priority", "status", "expires_at",
    ]
    assert sql == (
        "UPDATE memory_triggers SET "
        "trigger_condition = ?, trigger_keywords = ?, action = ?, "
        "priority = ?, status = ?, expires_at = ? "
        "WHERE id = ?"
    )
    assert params == ["cond", "k1,k2", "ping", "high", "active", "2026-01-01T00:00:00"]


def test_build_sql_all_none_returns_none_sql():
    sql, params, accepted, rejected = mcp_server._build_trigger_update_sql(
        {"trigger_condition": None, "priority": None}
    )
    assert sql is None
    assert accepted == []
    assert params == []


def test_allowlist_matches_schema_truth():
    """If init_schema.sql changes the memory_triggers column set, this test
    fails loudly so the allowlist gets re-synced rather than silently
    diverging."""
    schema_path = Path(__file__).resolve().parent.parent / "db" / "init_schema.sql"
    text = schema_path.read_text()
    start = text.index("CREATE TABLE memory_triggers")
    end = text.index(");", start)
    body = text[start:end]
    # crude column extractor: lines starting with a name token, not a constraint
    cols = []
    for raw in body.splitlines()[1:]:
        ln = raw.strip().rstrip(",")
        if not ln or ln.upper().startswith(("PRIMARY", "FOREIGN", "UNIQUE",
                                             "CHECK", "CREATE")):
            continue
        first = ln.split()[0]
        if first.isidentifier():
            cols.append(first)
    schema_cols = set(cols)
    # Intentionally excluded from the writable allowlist:
    #   id, created_at  — managed by SQLite, never updated by callers.
    #   agent_id        — ownership; changing it via update would be a
    #                      privilege-escalation path. Re-create instead.
    intentionally_excluded = {"id", "created_at", "agent_id"}
    expected_writable = schema_cols - intentionally_excluded

    # Allowlist must not contain anything the schema doesn't have (would
    # crash at execute time) and must cover every schema column we deliberately
    # left writable.
    assert set(ALLOWED) <= schema_cols, (
        f"Allowlist contains columns not in schema: {set(ALLOWED) - schema_cols}"
    )
    assert set(ALLOWED) == expected_writable, (
        "Allowlist drift detected. "
        f"Schema writable columns (excl. {intentionally_excluded}): {expected_writable}; "
        f"allowlist: {set(ALLOWED)}. Update _TRIGGER_UPDATE_ALLOWED_COLUMNS or "
        "the intentional-exclusion set."
    )


# ---------------------------------------------------------------------------
# Integration: drive tool_trigger_update against a real (temp) sqlite DB
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_brain_db(tmp_path, monkeypatch):
    """Stand up a minimal brain.db with just memory_triggers + agents + access_log
    so tool_trigger_update can run end-to-end without the full schema."""
    db_path = tmp_path / "brain.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE memory_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            trigger_condition TEXT NOT NULL,
            trigger_keywords TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_id INTEGER,
            memory_id INTEGER,
            priority TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','fired','expired','cancelled')),
            fired_at TEXT,
            expires_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE agents (
            id TEXT PRIMARY KEY,
            name TEXT,
            type TEXT,
            adapter_info TEXT,
            created_at TEXT
        );
        CREATE TABLE access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT, action TEXT, target_table TEXT, target_id INTEGER,
            query TEXT, result_count INTEGER, created_at TEXT
        );
        INSERT INTO memory_triggers
            (agent_id, trigger_condition, trigger_keywords, action, priority, status)
        VALUES
            ('test-agent', 'initial cond', 'a,b', 'ping', 'medium', 'active');
    """)
    conn.commit()
    conn.close()

    monkeypatch.setenv("BRAIN_DB", str(db_path))
    # mcp_server caches DB_PATH at import time; force a refresh
    monkeypatch.setattr(mcp_server, "DB_PATH", db_path, raising=False)
    yield db_path


def test_tool_trigger_update_valid_path_succeeds(temp_brain_db):
    """End-to-end: a valid call updates the row and reports the accepted
    columns in `updated_fields`."""
    result = mcp_server.tool_trigger_update(
        agent_id="test-agent",
        trigger_id=1,
        condition="updated cond",
        priority="high",
    )
    assert result["ok"] is True
    assert set(result["updated_fields"]) == {"trigger_condition", "priority"}

    # Verify the row actually moved
    conn = sqlite3.connect(str(temp_brain_db))
    row = conn.execute(
        "SELECT trigger_condition, priority FROM memory_triggers WHERE id = 1"
    ).fetchone()
    conn.close()
    assert row == ("updated cond", "high")


def test_tool_trigger_update_invalid_priority_rejected(temp_brain_db):
    """The CHECK-mirroring guard still fires before any SQL runs."""
    result = mcp_server.tool_trigger_update(
        agent_id="test-agent",
        trigger_id=1,
        priority="malicious'; DROP TABLE memory_triggers; --",
    )
    assert result["ok"] is False
    assert "Invalid priority" in result["error"]

    # Confirm table is untouched
    conn = sqlite3.connect(str(temp_brain_db))
    cnt = conn.execute("SELECT COUNT(*) FROM memory_triggers").fetchone()[0]
    conn.close()
    assert cnt == 1


def test_tool_trigger_update_no_fields_returns_error(temp_brain_db):
    result = mcp_server.tool_trigger_update(
        agent_id="test-agent", trigger_id=1,
    )
    assert result["ok"] is False
    assert result["error"] == "No fields to update"


def test_tool_trigger_update_missing_trigger_returns_error(temp_brain_db):
    result = mcp_server.tool_trigger_update(
        agent_id="test-agent", trigger_id=9999, condition="x",
    )
    assert result["ok"] is False
    assert "9999" in result["error"]
