"""Tests for V2-5: Phase-Aware Quantum Amplitude Scoring.

Papers / concepts: quantum cognition Wave 1, Busemeyer & Bruza 2012.

Covers:
- test_amplitude_from_confidence
- test_constructive_interference_boosts
- test_destructive_interference_reduces
- test_zero_confidence_zero_score
- test_bounded_zero_one
"""
from __future__ import annotations

import math
import sys
import os

# Ensure the package source is importable when running tests directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestQuantumAmplitude:
    def test_amplitude_from_confidence(self):
        from agentmemory._impl import _quantum_amplitude_score
        score = _quantum_amplitude_score(confidence=0.8, phase=0.0, neighbor_phases=[])
        assert abs(score - math.sqrt(0.8)) < 0.01

    def test_constructive_interference_boosts(self):
        from agentmemory._impl import _quantum_amplitude_score
        no_neighbors = _quantum_amplitude_score(0.5, 0.1, [])
        constructive = _quantum_amplitude_score(0.5, 0.1,
            [{"phase": 0.1, "weight": 0.8}, {"phase": 0.15, "weight": 0.7}])
        assert constructive >= no_neighbors

    def test_destructive_interference_reduces(self):
        from agentmemory._impl import _quantum_amplitude_score
        no_neighbors = _quantum_amplitude_score(0.5, 0.0, [])
        destructive = _quantum_amplitude_score(0.5, 0.0,
            [{"phase": math.pi, "weight": 0.8}])
        assert destructive <= no_neighbors

    def test_zero_confidence_zero_score(self):
        from agentmemory._impl import _quantum_amplitude_score
        assert _quantum_amplitude_score(0.0, 0.0, []) == 0.0

    def test_bounded_zero_one(self):
        from agentmemory._impl import _quantum_amplitude_score
        for conf in [0.1, 0.5, 0.9]:
            for phase in [0.0, 1.0, 3.14]:
                score = _quantum_amplitude_score(conf, phase, [])
                assert 0.0 <= score <= 1.0
