"""Tests for retrieval-practice strengthening.

Task 1: Retrieval-Practice Strengthening
Papers: Roediger & Karpicke 2006, Bjork 1994, Kehl et al. 2026

Covers:
- test_successful_recall_boosts_confidence
- test_hard_retrieval_boosts_more
- test_confidence_capped_at_one
- test_labile_window_reset_on_recall
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with just the memories columns the function touches."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE memories (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            content         TEXT NOT NULL DEFAULT '',
            confidence      REAL NOT NULL DEFAULT 0.7,
            alpha           REAL DEFAULT 1.0,
            beta            REAL DEFAULT 1.0,
            recalled_count  INTEGER NOT NULL DEFAULT 0,
            last_recalled_at TEXT DEFAULT NULL,
            labile_until    TEXT DEFAULT NULL,
            retrieval_prediction_error REAL DEFAULT NULL
        )
    """)
    db.commit()
    return db


def _insert_memory(db: sqlite3.Connection, confidence: float = 0.7) -> int:
    """Insert a test memory and return its id."""
    cur = db.execute(
        "INSERT INTO memories (content, confidence, alpha, beta, recalled_count) "
        "VALUES ('test memory', ?, 1.0, 1.0, 0)",
        (confidence,),
    )
    db.commit()
    return cur.lastrowid


def _fetch(db: sqlite3.Connection, memory_id: int) -> sqlite3.Row:
    return db.execute(
        "SELECT confidence, alpha, recalled_count, last_recalled_at, labile_until "
        "FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Import the function under test
# ---------------------------------------------------------------------------

from agentmemory._impl import _retrieval_practice_boost


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRetrievalPracticeBoost:
    def test_successful_recall_boosts_confidence(self):
        """Confidence must increase after a successful recall with zero RPE."""
        db = _make_db()
        mid = _insert_memory(db, confidence=0.7)
        before = dict(_fetch(db, mid))

        _retrieval_practice_boost(db, mid, retrieval_prediction_error=0.0)

        after = dict(_fetch(db, mid))
        assert after["confidence"] > before["confidence"], (
            f"Expected confidence to increase, got {before['confidence']} -> {after['confidence']}"
        )

    def test_hard_retrieval_boosts_more(self):
        """High RPE (hard retrieval) should produce a larger confidence boost than low RPE."""
        db = _make_db()
        mid_easy = _insert_memory(db, confidence=0.5)
        mid_hard = _insert_memory(db, confidence=0.5)

        _retrieval_practice_boost(db, mid_easy, retrieval_prediction_error=0.0)
        _retrieval_practice_boost(db, mid_hard, retrieval_prediction_error=1.0)

        easy = dict(_fetch(db, mid_easy))
        hard = dict(_fetch(db, mid_hard))

        assert hard["confidence"] > easy["confidence"], (
            f"Hard retrieval ({hard['confidence']}) should beat easy ({easy['confidence']})"
        )

    def test_confidence_capped_at_one(self):
        """Confidence must never exceed 1.0 even when starting near the cap."""
        db = _make_db()
        mid = _insert_memory(db, confidence=0.999)

        _retrieval_practice_boost(db, mid, retrieval_prediction_error=1.0)

        after = dict(_fetch(db, mid))
        assert after["confidence"] <= 1.0, (
            f"Confidence exceeded 1.0: {after['confidence']}"
        )

    def test_labile_window_reset_on_recall(self):
        """labile_until must be set to a non-null value after boost."""
        db = _make_db()
        mid = _insert_memory(db, confidence=0.5)
        before = dict(_fetch(db, mid))
        assert before["labile_until"] is None, "Precondition: labile_until should start as NULL"

        _retrieval_practice_boost(db, mid, retrieval_prediction_error=0.5)

        after = dict(_fetch(db, mid))
        assert after["labile_until"] is not None, (
            "labile_until should be set after boost"
        )

    def test_recalled_count_incremented(self):
        """recalled_count must increase by 1."""
        db = _make_db()
        mid = _insert_memory(db, confidence=0.5)

        _retrieval_practice_boost(db, mid, retrieval_prediction_error=0.0)

        after = dict(_fetch(db, mid))
        assert after["recalled_count"] == 1

    def test_alpha_incremented(self):
        """alpha must increase by 1.0 (Bayesian recall update)."""
        db = _make_db()
        mid = _insert_memory(db, confidence=0.5)

        _retrieval_practice_boost(db, mid, retrieval_prediction_error=0.0)

        after = dict(_fetch(db, mid))
        assert after["alpha"] == pytest.approx(2.0, abs=1e-6)

    def test_last_recalled_at_set(self):
        """last_recalled_at must be non-null after boost."""
        db = _make_db()
        mid = _insert_memory(db, confidence=0.5)
        before = dict(_fetch(db, mid))
        assert before["last_recalled_at"] is None

        _retrieval_practice_boost(db, mid, retrieval_prediction_error=0.0)

        after = dict(_fetch(db, mid))
        assert after["last_recalled_at"] is not None

    def test_none_rpe_treated_as_zero(self):
        """retrieval_prediction_error=None must behave the same as 0.0."""
        db = _make_db()
        mid_zero = _insert_memory(db, confidence=0.5)
        mid_none = _insert_memory(db, confidence=0.5)

        _retrieval_practice_boost(db, mid_zero, retrieval_prediction_error=0.0)
        _retrieval_practice_boost(db, mid_none, retrieval_prediction_error=None)

        r_zero = dict(_fetch(db, mid_zero))
        r_none = dict(_fetch(db, mid_none))

        assert r_zero["confidence"] == pytest.approx(r_none["confidence"], abs=1e-9)

    def test_rpe_clipped_above_one(self):
        """RPE values > 1.0 should behave the same as RPE = 1.0."""
        db = _make_db()
        mid_one = _insert_memory(db, confidence=0.5)
        mid_two = _insert_memory(db, confidence=0.5)

        _retrieval_practice_boost(db, mid_one, retrieval_prediction_error=1.0)
        _retrieval_practice_boost(db, mid_two, retrieval_prediction_error=5.0)

        r_one = dict(_fetch(db, mid_one))
        r_two = dict(_fetch(db, mid_two))

        assert r_one["confidence"] == pytest.approx(r_two["confidence"], abs=1e-9)

    def test_rpe_clipped_below_zero(self):
        """Negative RPE should behave the same as RPE = 0.0."""
        db = _make_db()
        mid_zero = _insert_memory(db, confidence=0.5)
        mid_neg = _insert_memory(db, confidence=0.5)

        _retrieval_practice_boost(db, mid_zero, retrieval_prediction_error=0.0)
        _retrieval_practice_boost(db, mid_neg, retrieval_prediction_error=-0.5)

        r_zero = dict(_fetch(db, mid_zero))
        r_neg = dict(_fetch(db, mid_neg))

        assert r_zero["confidence"] == pytest.approx(r_neg["confidence"], abs=1e-9)
