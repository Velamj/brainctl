"""Tests for Task C4: Q-Value Utility Scoring (Migration 042).

Papers: Zhang et al. 2026 / MemRL

Covers:
- test_positive_contribution_increases_q
- test_negative_contribution_decreases_q
- test_q_value_bounded_zero_one
- test_learning_rate_controls_speed
- test_q_value_affects_score
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import (
    _Q_LEARNING_RATE,
    _q_adjusted_score,
    _update_q_value,
)


# ---------------------------------------------------------------------------
# Minimal in-memory test DB
# ---------------------------------------------------------------------------

def _make_db(q_value=0.5):
    """Create a minimal in-memory DB with one memory row."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL DEFAULT 'test memory',
            q_value REAL DEFAULT 0.5,
            retired_at TEXT DEFAULT NULL
        );
    """)
    db.execute("INSERT INTO memories (content, q_value) VALUES (?, ?)", ("test memory", q_value))
    db.commit()
    return db


def _get_q(db, memory_id=1):
    row = db.execute("SELECT q_value FROM memories WHERE id=?", (memory_id,)).fetchone()
    return row["q_value"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUpdateQValue:

    def test_positive_contribution_increases_q(self):
        """TD update with contributed=True should increase Q above its starting value."""
        db = _make_db(q_value=0.5)
        _update_q_value(db, memory_id=1, contributed=True)
        q_new = _get_q(db)
        # reward=1.0, q_old=0.5 → q_new = 0.5 + 0.1*(1.0-0.5) = 0.55
        assert q_new == pytest.approx(0.55, abs=1e-9)
        assert q_new > 0.5

    def test_negative_contribution_decreases_q(self):
        """TD update with contributed=False should decrease Q below its starting value."""
        db = _make_db(q_value=0.5)
        _update_q_value(db, memory_id=1, contributed=False)
        q_new = _get_q(db)
        # reward=0.0, q_old=0.5 → q_new = 0.5 + 0.1*(0.0-0.5) = 0.45
        assert q_new == pytest.approx(0.45, abs=1e-9)
        assert q_new < 0.5

    def test_q_value_bounded_zero_one(self):
        """Q-value must stay in [0, 1] even after many extreme updates."""
        # Test upper bound: many positive updates from high starting Q
        db_high = _make_db(q_value=0.99)
        for _ in range(20):
            _update_q_value(db_high, memory_id=1, contributed=True)
        assert _get_q(db_high) <= 1.0

        # Test lower bound: many negative updates from low starting Q
        db_low = _make_db(q_value=0.01)
        for _ in range(20):
            _update_q_value(db_low, memory_id=1, contributed=False)
        assert _get_q(db_low) >= 0.0

    def test_learning_rate_controls_speed(self):
        """Higher learning_rate should produce a larger single-step delta."""
        db_fast = _make_db(q_value=0.5)
        db_slow = _make_db(q_value=0.5)

        _update_q_value(db_fast, memory_id=1, contributed=True, learning_rate=0.5)
        _update_q_value(db_slow, memory_id=1, contributed=True, learning_rate=0.1)

        q_fast = _get_q(db_fast)
        q_slow = _get_q(db_slow)

        # fast: 0.5 + 0.5*(1-0.5) = 0.75; slow: 0.5 + 0.1*(1-0.5) = 0.55
        assert q_fast == pytest.approx(0.75, abs=1e-9)
        assert q_slow == pytest.approx(0.55, abs=1e-9)
        assert q_fast > q_slow

    def test_no_op_for_missing_memory(self):
        """_update_q_value should silently skip non-existent or retired memory IDs."""
        db = _make_db(q_value=0.5)
        # Non-existent ID — should not raise
        _update_q_value(db, memory_id=999, contributed=True)
        # Retired memory — retire it first, then update
        db.execute("UPDATE memories SET retired_at='2000-01-01' WHERE id=1")
        db.commit()
        _update_q_value(db, memory_id=1, contributed=True)
        # Q-value should remain unchanged (row skipped due to retired_at IS NULL guard)
        q_after = _get_q(db)
        assert q_after == pytest.approx(0.5, abs=1e-9)


class TestQAdjustedScore:

    def test_q_value_affects_score(self):
        """Different Q-values should produce different adjusted scores for the same base."""
        base = 1.0
        score_low = _q_adjusted_score(base, q_value=0.0)    # 0.8x → 0.8
        score_mid = _q_adjusted_score(base, q_value=0.5)    # 1.0x → 1.0
        score_high = _q_adjusted_score(base, q_value=1.0)   # 1.2x → 1.2

        assert score_low == pytest.approx(0.8, abs=1e-9)
        assert score_mid == pytest.approx(1.0, abs=1e-9)
        assert score_high == pytest.approx(1.2, abs=1e-9)

        assert score_low < score_mid < score_high

    def test_none_q_value_is_neutral(self):
        """None Q-value should behave identically to Q=0.5 (neutral, 1.0x)."""
        base = 2.5
        assert _q_adjusted_score(base, q_value=None) == pytest.approx(
            _q_adjusted_score(base, q_value=0.5), abs=1e-9
        )

    def test_multiplier_range(self):
        """Multiplier must be in [0.8, 1.2] for all valid Q values."""
        base = 1.0
        eps = 1e-9
        for q in [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]:
            adjusted = _q_adjusted_score(base, q_value=q)
            # Allow tiny floating-point epsilon above/below the nominal [0.8, 1.2] bounds
            assert adjusted >= 0.8 - eps, f"Multiplier {adjusted} below 0.8 for q={q}"
            assert adjusted <= 1.2 + eps, f"Multiplier {adjusted} above 1.2 for q={q}"
