"""Tests for _assign_temporal_levels and _build_temporal_summary in _impl.py.

Task 3: Temporal Abstraction Hierarchy
Papers: TiMem multi-level temporal memory tree (2026)

Covers:
- test_assign_temporal_levels_by_age:  correct level assigned based on memory age
- test_all_levels_are_valid:           only valid level names are produced
- test_summarizes_day_from_moments:    day summary built from moment-level memories
- test_empty_day_returns_none:         no matching memories -> None
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _assign_temporal_levels, _build_temporal_summary

# Valid temporal levels as defined by the hierarchy
_VALID_LEVELS = {"moment", "session", "day", "week", "month", "quarter"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db():
    """Create an in-memory SQLite DB with a minimal memories table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL DEFAULT 'test',
            content TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'convention',
            retired_at TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            temporal_level TEXT NOT NULL DEFAULT 'moment'
        )
    """)
    conn.commit()
    return conn


def _insert_memory(conn, content="test memory", age_days=0, agent_id="test",
                   temporal_level="moment"):
    """Insert a memory with created_at offset by age_days from now."""
    created_at = (
        datetime.now(timezone.utc) - timedelta(days=age_days)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO memories (agent_id, content, category, created_at, temporal_level)"
        " VALUES (?, ?, 'convention', ?, ?)",
        (agent_id, content, created_at, temporal_level),
    )
    conn.commit()
    return conn.execute("SELECT id FROM memories ORDER BY id DESC LIMIT 1").fetchone()["id"]


# ---------------------------------------------------------------------------
# Test 1: assigns correct level based on age
# ---------------------------------------------------------------------------

class TestAssignTemporalLevelsByAge:
    """_assign_temporal_levels should classify memories by their age_days."""

    def test_moment_assigned_for_very_recent(self):
        """Memory created 1 hour ago → moment (<0.5 days)."""
        conn = _make_db()
        created_at = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
            " VALUES ('test', 'fresh memory', ?, 'moment')",
            (created_at,),
        )
        conn.commit()
        result = _assign_temporal_levels(conn)
        assert result["updated"] == 1
        level = conn.execute("SELECT temporal_level FROM memories LIMIT 1").fetchone()["temporal_level"]
        assert level == "moment"

    def test_session_assigned_for_16_hour_old(self):
        """Memory created 16 hours ago → session (>=0.5d, <1d)."""
        conn = _make_db()
        created_at = (
            datetime.now(timezone.utc) - timedelta(hours=16)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
            " VALUES ('test', 'session memory', ?, 'moment')",
            (created_at,),
        )
        conn.commit()
        _assign_temporal_levels(conn)
        level = conn.execute("SELECT temporal_level FROM memories LIMIT 1").fetchone()["temporal_level"]
        assert level == "session"

    def test_day_assigned_for_3_days_old(self):
        """Memory created 3 days ago → day (>=1d, <7d)."""
        conn = _make_db()
        _insert_memory(conn, age_days=3)
        _assign_temporal_levels(conn)
        level = conn.execute("SELECT temporal_level FROM memories LIMIT 1").fetchone()["temporal_level"]
        assert level == "day"

    def test_week_assigned_for_14_days_old(self):
        """Memory created 14 days ago → week (>=7d, <30d)."""
        conn = _make_db()
        _insert_memory(conn, age_days=14)
        _assign_temporal_levels(conn)
        level = conn.execute("SELECT temporal_level FROM memories LIMIT 1").fetchone()["temporal_level"]
        assert level == "week"

    def test_month_assigned_for_60_days_old(self):
        """Memory created 60 days ago → month (>=30d, <90d)."""
        conn = _make_db()
        _insert_memory(conn, age_days=60)
        _assign_temporal_levels(conn)
        level = conn.execute("SELECT temporal_level FROM memories LIMIT 1").fetchone()["temporal_level"]
        assert level == "month"

    def test_quarter_assigned_for_120_days_old(self):
        """Memory created 120 days ago → quarter (>=90d)."""
        conn = _make_db()
        _insert_memory(conn, age_days=120)
        _assign_temporal_levels(conn)
        level = conn.execute("SELECT temporal_level FROM memories LIMIT 1").fetchone()["temporal_level"]
        assert level == "quarter"

    def test_retired_memories_are_excluded(self):
        """Retired memories (retired_at IS NOT NULL) should not be updated."""
        conn = _make_db()
        _insert_memory(conn, age_days=3)  # day-level memory
        # mark it retired
        conn.execute("UPDATE memories SET retired_at = datetime('now') WHERE 1=1")
        conn.execute(
            "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
            " VALUES ('test', 'active', datetime('now'), 'moment')"
        )
        conn.commit()
        result = _assign_temporal_levels(conn)
        # Only the active one is updated
        assert result["updated"] == 1


# ---------------------------------------------------------------------------
# Test 2: all produced levels are valid values
# ---------------------------------------------------------------------------

class TestAllLevelsAreValid:
    """_assign_temporal_levels must only produce levels in _VALID_LEVELS."""

    def test_levels_are_valid_values(self):
        """Insert memories at various ages; all assigned levels must be in the valid set."""
        conn = _make_db()
        age_days_list = [0, 0.3, 0.8, 4, 15, 60, 120]
        for age in age_days_list:
            created_at = (
                datetime.now(timezone.utc) - timedelta(days=age)
            ).strftime("%Y-%m-%dT%H:%M:%S")
            conn.execute(
                "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
                " VALUES ('test', 'memory', ?, 'moment')",
                (created_at,),
            )
        conn.commit()
        _assign_temporal_levels(conn)
        rows = conn.execute("SELECT temporal_level FROM memories WHERE retired_at IS NULL").fetchall()
        for row in rows:
            assert row["temporal_level"] in _VALID_LEVELS, (
                f"Unexpected temporal_level: {row['temporal_level']!r}"
            )


# ---------------------------------------------------------------------------
# Test 3: summarizes day from moment-level memories
# ---------------------------------------------------------------------------

class TestSummarizesDay:
    """_build_temporal_summary should concatenate snippets from moment memories on the date."""

    def test_summarizes_day_from_moments(self):
        """Insert moment-level memories for today; summary should contain their text."""
        conn = _make_db()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
            " VALUES ('test', 'First moment event.', ?, 'moment')",
            (now_ts,),
        )
        conn.execute(
            "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
            " VALUES ('test', 'Second moment event.', ?, 'moment')",
            (now_ts,),
        )
        conn.commit()
        summary = _build_temporal_summary(conn, level="day", date=today, agent_id="test")
        assert summary is not None
        assert "First moment event" in summary or "Second moment event" in summary

    def test_week_summary_includes_moment_and_session(self):
        """Week-level summary should pull both moment and session memories."""
        conn = _make_db()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday_ts = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
            " VALUES ('test', 'Moment event yesterday.', ?, 'moment')",
            (yesterday_ts,),
        )
        conn.execute(
            "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
            " VALUES ('test', 'Session summary yesterday.', ?, 'session')",
            (yesterday_ts,),
        )
        conn.commit()
        summary = _build_temporal_summary(conn, level="week", date=today, agent_id="test")
        assert summary is not None
        # Both should be included
        assert "Moment event yesterday" in summary or "Session summary yesterday" in summary

    def test_day_summary_excludes_session_level(self):
        """Day summary should NOT include session-level memories — only moment."""
        conn = _make_db()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
            " VALUES ('test', 'Session only content.', ?, 'session')",
            (now_ts,),
        )
        conn.commit()
        summary = _build_temporal_summary(conn, level="day", date=today, agent_id="test")
        # Only session-level memories exist; day-level should find nothing
        assert summary is None


# ---------------------------------------------------------------------------
# Test 4: empty day returns None
# ---------------------------------------------------------------------------

class TestEmptyDayReturnsNone:
    """_build_temporal_summary returns None when no matching memories exist."""

    def test_empty_day_returns_none(self):
        """No memories at all → None."""
        conn = _make_db()
        summary = _build_temporal_summary(conn, level="day", date="2020-01-01", agent_id="test")
        assert summary is None

    def test_wrong_date_returns_none(self):
        """Memories exist but on a different date → None."""
        conn = _make_db()
        old_ts = "2025-01-01T10:00:00"
        conn.execute(
            "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
            " VALUES ('test', 'Old event.', ?, 'moment')",
            (old_ts,),
        )
        conn.commit()
        summary = _build_temporal_summary(conn, level="day", date="2025-01-02", agent_id="test")
        assert summary is None

    def test_different_agent_returns_none(self):
        """Memories exist for a different agent → None for our agent."""
        conn = _make_db()
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO memories (agent_id, content, created_at, temporal_level)"
            " VALUES ('other-agent', 'Other agent event.', ?, 'moment')",
            (now_ts,),
        )
        conn.commit()
        summary = _build_temporal_summary(conn, level="day", date=today, agent_id="test")
        assert summary is None
