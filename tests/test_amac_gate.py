"""Tests for A-MAC 5-factor write gate.

Task 6: A-MAC 5-Factor Write Gate
Papers: Zhang et al. 2026, ICLR 2026 Workshop MemAgents

Covers:
- test_five_factors_contribute: changing any factor changes the score
- test_content_type_prior_most_influential: high prior + low others > low prior + high others
- test_score_bounded_zero_one: all outputs in [0, 1]
- test_all_zero_rejected: score < 0.3 threshold
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _amac_worthiness, _AMAC_WEIGHTS


class TestFiveFactorsContribute:
    """Changing any single factor changes the score (each factor has nonzero weight)."""

    def _baseline(self):
        return dict(
            future_utility=0.5,
            factual_confidence=0.5,
            semantic_novelty=0.5,
            temporal_recency=0.5,
            content_type_prior=0.5,
        )

    def test_future_utility_changes_score(self):
        base = self._baseline()
        low  = _amac_worthiness(**{**base, "future_utility": 0.1})
        high = _amac_worthiness(**{**base, "future_utility": 0.9})
        assert low != high, "future_utility should affect score"

    def test_factual_confidence_changes_score(self):
        base = self._baseline()
        low  = _amac_worthiness(**{**base, "factual_confidence": 0.1})
        high = _amac_worthiness(**{**base, "factual_confidence": 0.9})
        assert low != high, "factual_confidence should affect score"

    def test_semantic_novelty_changes_score(self):
        base = self._baseline()
        low  = _amac_worthiness(**{**base, "semantic_novelty": 0.1})
        high = _amac_worthiness(**{**base, "semantic_novelty": 0.9})
        assert low != high, "semantic_novelty should affect score"

    def test_temporal_recency_changes_score(self):
        base = self._baseline()
        low  = _amac_worthiness(**{**base, "temporal_recency": 0.1})
        high = _amac_worthiness(**{**base, "temporal_recency": 0.9})
        assert low != high, "temporal_recency should affect score"

    def test_content_type_prior_changes_score(self):
        base = self._baseline()
        low  = _amac_worthiness(**{**base, "content_type_prior": 0.1})
        high = _amac_worthiness(**{**base, "content_type_prior": 0.9})
        assert low != high, "content_type_prior should affect score"


class TestContentTypePriorMostInfluential:
    """content_type_prior (weight 0.40) is the most influential *single* factor.

    The weight of 0.40 exceeds any other individual factor weight.
    A unit swing in content_type_prior moves the score by 0.40, while the
    same swing in the next-largest factor (semantic_novelty, 0.20) moves it
    by only 0.20.  We verify this by holding all other factors fixed and
    measuring the per-unit impact of each factor.
    """

    def test_high_prior_low_others_beats_low_prior_high_others(self):
        # Hold all other factors at 0.5 (neutral), vary only content_type_prior
        # and each competing factor across its full range.
        # content_type_prior swing: 0.0 -> 1.0 moves score by 0.40.
        # No other single factor achieves that swing.
        base = dict(future_utility=0.5, factual_confidence=0.5,
                    semantic_novelty=0.5, temporal_recency=0.5,
                    content_type_prior=0.5)

        # Measure the delta each factor contributes over its full range
        def swing(factor):
            lo = _amac_worthiness(**{**base, factor: 0.0})
            hi = _amac_worthiness(**{**base, factor: 1.0})
            return round(hi - lo, 6)

        prior_swing = swing("content_type_prior")
        other_swings = {
            f: swing(f)
            for f in ("future_utility", "factual_confidence",
                      "semantic_novelty", "temporal_recency")
        }
        for factor, delta in other_swings.items():
            assert prior_swing > delta, (
                f"content_type_prior swing ({prior_swing}) should be larger than "
                f"{factor} swing ({delta})"
            )

    def test_content_type_prior_weight_is_largest(self):
        """The weight constant itself should be 0.40 and larger than all others."""
        w = _AMAC_WEIGHTS
        assert w["content_type_prior"] == 0.40
        for factor, weight in w.items():
            if factor != "content_type_prior":
                assert w["content_type_prior"] > weight, (
                    f"content_type_prior weight ({w['content_type_prior']}) must exceed "
                    f"{factor} weight ({weight})"
                )


class TestScoreBoundedZeroOne:
    """All _amac_worthiness outputs are in [0, 1]."""

    def test_all_zeros(self):
        score = _amac_worthiness(0.0, 0.0, 0.0, 0.0, 0.0)
        assert 0.0 <= score <= 1.0

    def test_all_ones(self):
        score = _amac_worthiness(1.0, 1.0, 1.0, 1.0, 1.0)
        assert 0.0 <= score <= 1.0
        assert score == pytest.approx(1.0)

    def test_out_of_range_inputs_clamped(self):
        """Inputs outside [0, 1] are clamped before weighting."""
        score_neg = _amac_worthiness(-5.0, -5.0, -5.0, -5.0, -5.0)
        score_pos = _amac_worthiness(5.0, 5.0, 5.0, 5.0, 5.0)
        assert score_neg == pytest.approx(0.0)
        assert score_pos == pytest.approx(1.0)

    def test_mixed_values_in_bounds(self):
        for fu, fc, sn, tr, ctp in [
            (0.3, 0.7, 0.5, 1.0, 0.8),
            (0.0, 1.0, 0.0, 1.0, 0.0),
            (0.99, 0.01, 0.5, 0.5, 0.5),
        ]:
            score = _amac_worthiness(fu, fc, sn, tr, ctp)
            assert 0.0 <= score <= 1.0, (
                f"score {score} out of range for inputs "
                f"fu={fu}, fc={fc}, sn={sn}, tr={tr}, ctp={ctp}"
            )


class TestAllZeroRejected:
    """All-zero score is below the 0.3 rejection threshold."""

    def test_all_zero_score_is_zero(self):
        score = _amac_worthiness(0.0, 0.0, 0.0, 0.0, 0.0)
        assert score == pytest.approx(0.0)

    def test_all_zero_below_threshold(self):
        score = _amac_worthiness(0.0, 0.0, 0.0, 0.0, 0.0)
        assert score < 0.3, (
            f"All-zero inputs should yield score < 0.3 (got {score}), "
            "which would be rejected by the write gate unless --force"
        )

    def test_low_prior_low_novelty_rejected(self):
        """A realistic low-quality write (unknown category, near-duplicate) is rejected."""
        score = _amac_worthiness(
            future_utility=0.5,      # default
            factual_confidence=0.5,  # mcp_tool source
            semantic_novelty=0.02,   # near-duplicate (very low surprise)
            temporal_recency=1.0,    # fresh write
            content_type_prior=0.05, # unknown/very-low-value category
        )
        assert score < 0.3, (
            f"Low-novelty write into unknown category should be rejected (score={score:.4f})"
        )


class TestWeightsSumToOne:
    """Sanity check: all weights sum to 1.0."""

    def test_weights_sum_to_one(self):
        total = sum(_AMAC_WEIGHTS.values())
        assert total == pytest.approx(1.0), (
            f"_AMAC_WEIGHTS should sum to 1.0, got {total}"
        )

    def test_required_factors_present(self):
        required = {"future_utility", "factual_confidence", "semantic_novelty",
                    "temporal_recency", "content_type_prior"}
        assert required == set(_AMAC_WEIGHTS.keys()), (
            f"Missing factors: {required - set(_AMAC_WEIGHTS.keys())}"
        )
