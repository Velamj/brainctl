"""Tests for Modification Resistance for Reconsolidation.

Task 4: Modification Resistance for Reconsolidation
Papers: O'Neill & Winters 2026, Neuroscience

Memories develop resistance to reconsolidation that increases with age, recall
count, and EWC importance. The surprise signal must exceed this resistance to
open a labile window.

Covers:
- test_young_memory_low_resistance
- test_old_frequent_memory_high_resistance
- test_resistance_capped_below_one
- test_surprise_must_exceed_resistance
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _modification_resistance, _should_open_labile_window


class TestModificationResistance:
    def test_young_memory_low_resistance(self):
        """A new memory with few recalls should have resistance < 0.2."""
        resistance = _modification_resistance(
            days_old=0,
            recalled_count=1,
            ewc_importance=0.0,
        )
        assert resistance < 0.2, (
            f"Expected resistance < 0.2 for new memory, got {resistance}"
        )

    def test_old_frequent_memory_high_resistance(self):
        """An old, frequently recalled, high-EWC memory should have resistance > 0.6."""
        resistance = _modification_resistance(
            days_old=365,
            recalled_count=20,
            ewc_importance=1.0,
        )
        assert resistance > 0.6, (
            f"Expected resistance > 0.6 for old/frequent/important memory, got {resistance}"
        )

    def test_resistance_capped_below_one(self):
        """Maximum resistance must never exceed 0.9."""
        resistance = _modification_resistance(
            days_old=10000,
            recalled_count=10000,
            ewc_importance=1.0,
        )
        assert resistance <= 0.9, (
            f"Expected resistance <= 0.9 (capped), got {resistance}"
        )

    def test_surprise_must_exceed_resistance(self):
        """_should_open_labile_window returns True when surprise > resistance, False otherwise."""
        assert _should_open_labile_window(surprise=0.9, resistance=0.3) is True, (
            "Expected True when surprise > resistance"
        )
        assert _should_open_labile_window(surprise=0.3, resistance=0.9) is False, (
            "Expected False when surprise < resistance"
        )
        # Edge case: exactly equal — should NOT open (strict greater-than)
        assert _should_open_labile_window(surprise=0.5, resistance=0.5) is False, (
            "Expected False when surprise == resistance (strict >)"
        )

    def test_ewc_none_treated_as_zero(self):
        """ewc_importance=None should be handled gracefully, treated as 0.0."""
        r_none = _modification_resistance(days_old=1, recalled_count=0, ewc_importance=None)
        r_zero = _modification_resistance(days_old=1, recalled_count=0, ewc_importance=0.0)
        assert r_none == pytest.approx(r_zero, abs=1e-9), (
            f"None ewc_importance should equal 0.0 result: {r_none} vs {r_zero}"
        )

    def test_negative_days_clamped_to_zero(self):
        """Negative days_old should be clamped to 0 (future-dated memory)."""
        r_neg = _modification_resistance(days_old=-5, recalled_count=0, ewc_importance=0.0)
        r_zero = _modification_resistance(days_old=0, recalled_count=0, ewc_importance=0.0)
        assert r_neg == pytest.approx(r_zero, abs=1e-9), (
            f"Negative days should equal 0-day result: {r_neg} vs {r_zero}"
        )

    def test_negative_recalled_count_clamped(self):
        """Negative recalled_count should be clamped to 0."""
        r_neg = _modification_resistance(days_old=0, recalled_count=-10, ewc_importance=0.0)
        r_zero = _modification_resistance(days_old=0, recalled_count=0, ewc_importance=0.0)
        assert r_neg == pytest.approx(r_zero, abs=1e-9), (
            f"Negative recalled_count should equal 0 result: {r_neg} vs {r_zero}"
        )

    def test_ewc_clipped_above_one(self):
        """ewc_importance > 1.0 should be clamped to 1.0."""
        r_one = _modification_resistance(days_old=0, recalled_count=0, ewc_importance=1.0)
        r_over = _modification_resistance(days_old=0, recalled_count=0, ewc_importance=5.0)
        assert r_one == pytest.approx(r_over, abs=1e-9), (
            f"ewc_importance > 1.0 should equal 1.0 result: {r_one} vs {r_over}"
        )

    def test_age_term_increases_with_days(self):
        """Older memories should have higher resistance than younger ones."""
        r_young = _modification_resistance(days_old=1, recalled_count=0, ewc_importance=0.0)
        r_old = _modification_resistance(days_old=100, recalled_count=0, ewc_importance=0.0)
        assert r_old > r_young, (
            f"Older memory should have higher resistance: {r_old} vs {r_young}"
        )

    def test_recall_term_increases_with_count(self):
        """More recalls should increase resistance."""
        r_few = _modification_resistance(days_old=0, recalled_count=2, ewc_importance=0.0)
        r_many = _modification_resistance(days_old=0, recalled_count=10, ewc_importance=0.0)
        assert r_many > r_few, (
            f"More recalls should increase resistance: {r_many} vs {r_few}"
        )

    def test_ewc_term_increases_with_importance(self):
        """Higher EWC importance should increase resistance."""
        r_low = _modification_resistance(days_old=0, recalled_count=0, ewc_importance=0.1)
        r_high = _modification_resistance(days_old=0, recalled_count=0, ewc_importance=0.9)
        assert r_high > r_low, (
            f"Higher EWC importance should increase resistance: {r_high} vs {r_low}"
        )
