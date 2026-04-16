"""Tests for W(m) Gate Calibration Feedback Loop.

Task 7: Gate Calibration Score
Papers: Dunlosky & Metcalfe 2009, Nelson & Narens 1990

Covers:
- test_calibration_with_no_memories_returns_none
- test_calibration_with_few_memories_returns_none
- test_well_calibrated_gate
- test_poorly_calibrated_gate
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
    """Create an in-memory SQLite DB with the columns _gate_calibration_score touches."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE memories (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            content         TEXT NOT NULL DEFAULT '',
            confidence      REAL NOT NULL DEFAULT 0.7,
            recalled_count  INTEGER NOT NULL DEFAULT 0,
            retired_at      TEXT DEFAULT NULL
        )
    """)
    db.commit()
    return db


def _insert_memory(
    db: sqlite3.Connection,
    confidence: float,
    recalled_count: int,
    retired: bool = False,
) -> int:
    """Insert a test memory and return its id."""
    cur = db.execute(
        "INSERT INTO memories (content, confidence, recalled_count, retired_at) "
        "VALUES ('test memory', ?, ?, ?)",
        (confidence, recalled_count, "2024-01-01" if retired else None),
    )
    db.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Import the function under test
# ---------------------------------------------------------------------------

from agentmemory._impl import _gate_calibration_score


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGateCalibration:
    def test_calibration_with_no_memories_returns_none(self):
        """Empty brain should return None (insufficient data)."""
        db = _make_db()
        result = _gate_calibration_score(db)
        assert result is None, f"Expected None for empty DB, got {result}"

    def test_calibration_with_few_memories_returns_none(self):
        """Fewer than 10 memories should return None."""
        db = _make_db()
        # Insert 9 memories (one short of the threshold)
        for i in range(9):
            _insert_memory(db, confidence=0.5 + i * 0.05, recalled_count=i)

        result = _gate_calibration_score(db)
        assert result is None, (
            f"Expected None for 9 memories (< 10), got {result}"
        )

    def test_well_calibrated_gate(self):
        """If high-confidence memories are recalled more, correlation > 0."""
        db = _make_db()
        # Low confidence + low recall_count
        for i in range(5):
            _insert_memory(db, confidence=0.1 + i * 0.02, recalled_count=i)
        # High confidence + high recall_count
        for i in range(5):
            _insert_memory(db, confidence=0.8 + i * 0.02, recalled_count=10 + i * 2)

        result = _gate_calibration_score(db)
        assert result is not None, "Expected a float, got None"
        assert result > 0.0, (
            f"Expected positive correlation for well-calibrated gate, got {result}"
        )

    def test_poorly_calibrated_gate(self):
        """If high-confidence memories are recalled LESS, correlation < 0."""
        db = _make_db()
        # Low confidence + high recall_count
        for i in range(5):
            _insert_memory(db, confidence=0.1 + i * 0.02, recalled_count=10 + i * 2)
        # High confidence + low recall_count
        for i in range(5):
            _insert_memory(db, confidence=0.8 + i * 0.02, recalled_count=i)

        result = _gate_calibration_score(db)
        assert result is not None, "Expected a float, got None"
        assert result < 0.0, (
            f"Expected negative correlation for miscalibrated gate, got {result}"
        )

    def test_exactly_ten_memories_produces_result(self):
        """Exactly 10 memories should produce a non-None result."""
        db = _make_db()
        for i in range(10):
            _insert_memory(db, confidence=0.1 * (i + 1), recalled_count=i)

        result = _gate_calibration_score(db)
        assert result is not None, "Expected a float for exactly 10 memories, got None"

    def test_retired_memories_excluded(self):
        """Retired memories (retired_at IS NOT NULL) must be excluded."""
        db = _make_db()
        # Insert 10 retired memories that would show high positive correlation
        for i in range(10):
            _insert_memory(db, confidence=0.8 + i * 0.02, recalled_count=10 + i, retired=True)
        # Only 5 active memories — below threshold
        for i in range(5):
            _insert_memory(db, confidence=0.5, recalled_count=i)

        result = _gate_calibration_score(db)
        assert result is None, (
            f"Expected None when active count < 10 (retired excluded), got {result}"
        )

    def test_constant_confidence_returns_zero(self):
        """If all memories have the same confidence, std_c = 0 → return 0.0."""
        db = _make_db()
        for i in range(10):
            _insert_memory(db, confidence=0.5, recalled_count=i)

        result = _gate_calibration_score(db)
        assert result == pytest.approx(0.0, abs=1e-9), (
            f"Expected 0.0 when all confidence values are identical, got {result}"
        )

    def test_constant_recall_count_returns_zero(self):
        """If all memories have the same recalled_count, std_r = 0 → return 0.0."""
        db = _make_db()
        for i in range(10):
            _insert_memory(db, confidence=0.1 * (i + 1), recalled_count=5)

        result = _gate_calibration_score(db)
        assert result == pytest.approx(0.0, abs=1e-9), (
            f"Expected 0.0 when all recalled_count values are identical, got {result}"
        )

    def test_correlation_range(self):
        """Correlation coefficient must be in [-1.0, 1.0]."""
        db = _make_db()
        # Perfect positive correlation: confidence == recalled_count / 10
        for i in range(10):
            _insert_memory(db, confidence=0.1 * (i + 1), recalled_count=i + 1)

        result = _gate_calibration_score(db)
        assert result is not None
        assert -1.0 <= result <= 1.0, (
            f"Correlation {result} out of valid range [-1, 1]"
        )

    def test_perfect_positive_correlation(self):
        """Perfectly correlated data → correlation close to 1.0."""
        db = _make_db()
        for i in range(10):
            _insert_memory(db, confidence=(i + 1) / 10.0, recalled_count=i + 1)

        result = _gate_calibration_score(db)
        assert result is not None
        assert result == pytest.approx(1.0, abs=1e-6), (
            f"Expected ~1.0 for perfect positive correlation, got {result}"
        )
