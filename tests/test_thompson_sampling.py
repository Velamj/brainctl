"""Tests for Thompson Sampling retrieval confidence injection.

Task 2: Thompson Sampling Retrieval
Papers: Thompson 1933, Glowacka 2019

Covers:
- test_thompson_sample_within_bounds
- test_high_alpha_biases_high
- test_high_beta_biases_low
- test_uncertain_memory_explores
- test_certain_memory_exploits
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _thompson_confidence

# Number of samples for stochastic tests — large enough to be reliable,
# small enough to finish in well under a second.
N_SAMPLES = 2_000


class TestThompsonConfidence:
    def test_thompson_sample_within_bounds(self):
        """Every sample from Beta(alpha, beta) must be in [0, 1]."""
        rng = random.Random(42)
        _orig = random.betavariate
        try:
            # Patch module-level random with seeded version so test is reproducible
            random.betavariate = rng.betavariate
            for _ in range(N_SAMPLES):
                v = _thompson_confidence(alpha=1.0, beta=1.0)
                assert 0.0 <= v <= 1.0, f"Sample {v!r} out of [0, 1]"
        finally:
            random.betavariate = _orig

    def test_high_alpha_biases_high(self):
        """Beta(100, 1) should produce mean close to 1.0 (alpha dominates)."""
        rng = random.Random(123)
        _orig = random.betavariate
        try:
            random.betavariate = rng.betavariate
            samples = [_thompson_confidence(alpha=100.0, beta=1.0) for _ in range(N_SAMPLES)]
        finally:
            random.betavariate = _orig
        mean = sum(samples) / N_SAMPLES
        # Beta(100, 1) has mean 100/101 ≈ 0.99; we require > 0.90
        assert mean > 0.90, f"High-alpha mean too low: {mean:.4f}"

    def test_high_beta_biases_low(self):
        """Beta(1, 100) should produce mean close to 0.0 (beta dominates)."""
        rng = random.Random(456)
        _orig = random.betavariate
        try:
            random.betavariate = rng.betavariate
            samples = [_thompson_confidence(alpha=1.0, beta=100.0) for _ in range(N_SAMPLES)]
        finally:
            random.betavariate = _orig
        mean = sum(samples) / N_SAMPLES
        # Beta(1, 100) has mean 1/101 ≈ 0.0099; we require < 0.10
        assert mean < 0.10, f"High-beta mean too high: {mean:.4f}"

    def test_uncertain_memory_explores(self):
        """Beta(1, 1) (uniform prior) should have high variance — stddev > 0.20."""
        rng = random.Random(789)
        _orig = random.betavariate
        try:
            random.betavariate = rng.betavariate
            samples = [_thompson_confidence(alpha=1.0, beta=1.0) for _ in range(N_SAMPLES)]
        finally:
            random.betavariate = _orig
        mean = sum(samples) / N_SAMPLES
        variance = sum((x - mean) ** 2 for x in samples) / N_SAMPLES
        stddev = variance ** 0.5
        # Beta(1,1) is uniform on [0,1]; stddev = 1/sqrt(12) ≈ 0.289
        assert stddev > 0.20, f"Uncertain memory stddev too low: {stddev:.4f}"

    def test_certain_memory_exploits(self):
        """Beta(100, 1) (very certain positive) should have low variance — stddev < 0.05."""
        rng = random.Random(321)
        _orig = random.betavariate
        try:
            random.betavariate = rng.betavariate
            samples = [_thompson_confidence(alpha=100.0, beta=1.0) for _ in range(N_SAMPLES)]
        finally:
            random.betavariate = _orig
        mean = sum(samples) / N_SAMPLES
        variance = sum((x - mean) ** 2 for x in samples) / N_SAMPLES
        stddev = variance ** 0.5
        # Beta(100,1) has stddev = sqrt(100*1/(101^2*102)) ≈ 0.0098
        assert stddev < 0.05, f"Certain memory stddev too high: {stddev:.4f}"

    def test_none_alpha_uses_default(self):
        """alpha=None should fall back to 1.0 without raising."""
        # Must not raise; result should be in [0, 1]
        v = _thompson_confidence(alpha=None, beta=1.0)
        assert 0.0 <= v <= 1.0

    def test_none_beta_uses_default(self):
        """beta=None should fall back to 1.0 without raising."""
        v = _thompson_confidence(alpha=1.0, beta=None)
        assert 0.0 <= v <= 1.0

    def test_zero_alpha_clamped(self):
        """alpha=0.0 should be clamped to 0.01 without raising."""
        v = _thompson_confidence(alpha=0.0, beta=1.0)
        assert 0.0 <= v <= 1.0

    def test_zero_beta_clamped(self):
        """beta=0.0 should be clamped to 0.01 without raising."""
        v = _thompson_confidence(alpha=1.0, beta=0.0)
        assert 0.0 <= v <= 1.0

    def test_negative_alpha_clamped(self):
        """Negative alpha should be clamped to 0.01 without raising."""
        v = _thompson_confidence(alpha=-5.0, beta=1.0)
        assert 0.0 <= v <= 1.0

    def test_default_args_produce_valid_sample(self):
        """Calling with no args (defaults) should return a value in [0, 1]."""
        v = _thompson_confidence()
        assert 0.0 <= v <= 1.0
