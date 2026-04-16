"""Tests for Task C3: Spaced-Review Scheduler (Migration 041).

Covers compute_review_interval_hours, schedule_spaced_reviews,
and process_due_reviews from agentmemory.hippocampus.
"""
import sqlite3
import pytest

from agentmemory.hippocampus import (
    compute_review_interval_hours,
    schedule_spaced_reviews,
    process_due_reviews,
    _RETENTION_INTERVALS,
)


# ---------------------------------------------------------------------------
# Minimal in-memory test DB
# ---------------------------------------------------------------------------

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
            last_recalled_at TEXT DEFAULT NULL,
            next_review_at TEXT DEFAULT NULL,
            retired_at TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    return db


# ---------------------------------------------------------------------------
# TestComputeReviewInterval
# ---------------------------------------------------------------------------

class TestComputeReviewInterval:
    """3 tests for compute_review_interval_hours."""

    def test_medium_default_stability(self):
        """medium class at stability=1.0 → 23 * 0.15 * 24 hours."""
        expected = _RETENTION_INTERVALS["medium"] * 0.15 * 24 * 1.0
        assert compute_review_interval_hours("medium", 1.0) == pytest.approx(expected)

    def test_all_temporal_classes_give_different_intervals(self):
        """Each temporal class produces a distinct interval."""
        classes = ["ephemeral", "short", "medium", "long", "permanent"]
        intervals = [compute_review_interval_hours(tc, 1.0) for tc in classes]
        assert len(set(intervals)) == len(intervals), "All intervals should be distinct"
        # intervals should be monotonically increasing with retention interval
        for i in range(len(intervals) - 1):
            assert intervals[i] < intervals[i + 1]

    def test_stability_clamping(self):
        """stability is clamped to [0.5, 10.0] — values outside range are bounded."""
        base = _RETENTION_INTERVALS["medium"] * 0.15 * 24
        # Below-floor stability should equal floor (0.5)
        low = compute_review_interval_hours("medium", 0.01)
        assert low == pytest.approx(base * 0.5)
        # Above-ceiling stability should equal ceiling (10.0)
        high = compute_review_interval_hours("medium", 999.0)
        assert high == pytest.approx(base * 10.0)


# ---------------------------------------------------------------------------
# TestScheduleReviews
# ---------------------------------------------------------------------------

class TestScheduleReviews:
    """3 tests for schedule_spaced_reviews."""

    def test_schedules_unscheduled_memories(self):
        """Active memories with no next_review_at get scheduled."""
        db = _make_db()
        db.execute(
            "INSERT INTO memories (content, confidence, temporal_class) VALUES (?, ?, ?)",
            ("fact A", 0.8, "medium"),
        )
        db.execute(
            "INSERT INTO memories (content, confidence, temporal_class) VALUES (?, ?, ?)",
            ("fact B", 0.9, "short"),
        )
        db.commit()

        result = schedule_spaced_reviews(db)
        assert result["scheduled"] == 2

        rows = db.execute(
            "SELECT next_review_at FROM memories WHERE retired_at IS NULL"
        ).fetchall()
        assert all(r["next_review_at"] is not None for r in rows)

    def test_skips_already_scheduled(self):
        """Memories that already have next_review_at are left untouched."""
        db = _make_db()
        db.execute(
            "INSERT INTO memories (content, confidence, next_review_at) VALUES (?, ?, ?)",
            ("already scheduled", 0.9, "2099-01-01T00:00:00"),
        )
        db.commit()

        result = schedule_spaced_reviews(db)
        assert result["scheduled"] == 0

        row = db.execute("SELECT next_review_at FROM memories").fetchone()
        assert row["next_review_at"] == "2099-01-01T00:00:00"

    def test_skips_low_confidence(self):
        """Memories below min_confidence threshold are not scheduled."""
        db = _make_db()
        db.execute(
            "INSERT INTO memories (content, confidence) VALUES (?, ?)",
            ("low confidence memory", 0.1),
        )
        db.commit()

        result = schedule_spaced_reviews(db, min_confidence=0.3)
        assert result["scheduled"] == 0

        row = db.execute("SELECT next_review_at FROM memories").fetchone()
        assert row["next_review_at"] is None


# ---------------------------------------------------------------------------
# TestProcessDueReviews
# ---------------------------------------------------------------------------

class TestProcessDueReviews:
    """2 tests for process_due_reviews."""

    def test_processes_due_memories(self):
        """Due memories get recalled_count incremented and next_review_at rescheduled."""
        db = _make_db()
        # Set next_review_at in the past to make it due
        db.execute(
            """INSERT INTO memories (content, confidence, temporal_class, stability,
               recalled_count, next_review_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("due memory", 0.8, "medium", 1.0, 0, "2000-01-01T00:00:00"),
        )
        db.commit()

        result = process_due_reviews(db)
        assert result["reviewed"] == 1

        row = db.execute(
            "SELECT recalled_count, last_recalled_at, next_review_at FROM memories"
        ).fetchone()
        assert row["recalled_count"] == 1
        assert row["last_recalled_at"] is not None
        # next_review_at should have been rescheduled to the future
        assert row["next_review_at"] > "2000-01-01T00:00:00"

    def test_skips_future_reviews(self):
        """Memories with future next_review_at are not processed."""
        db = _make_db()
        db.execute(
            """INSERT INTO memories (content, confidence, temporal_class,
               recalled_count, next_review_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("future memory", 0.8, "medium", 0, "2099-12-31T23:59:59"),
        )
        db.commit()

        result = process_due_reviews(db)
        assert result["reviewed"] == 0

        row = db.execute(
            "SELECT recalled_count, next_review_at FROM memories"
        ).fetchone()
        assert row["recalled_count"] == 0
        assert row["next_review_at"] == "2099-12-31T23:59:59"
