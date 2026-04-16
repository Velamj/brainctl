"""Tests for Context-Matching Reranker (Task C2).

Papers: Smith & Vela 2001, Heald et al. 2023, HippoRAG 2024

Covers:
- test_exact_hash_match_high_score
- test_no_context_returns_zero
- test_partial_overlap_positive
- test_no_overlap_returns_zero
- test_score_bounded_0_1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _context_match_score


class TestContextMatchScore:
    """Unit tests for _context_match_score pure function."""

    def test_exact_hash_match_high_score(self):
        """When memory_hash == current_hash, score gets 0.3 base bonus.

        With a full hash match and identical key-value context, the total
        should be 0.3 (hash bonus) + 0.7 * 1.0 (full overlap) = 1.0.
        """
        ctx = json.dumps({"project": "brainctl", "agent_id": "agent-1"})
        hash_val = "abc123def456abcd"  # 16-char hex hash

        score = _context_match_score(ctx, hash_val, ctx, hash_val)

        # 0.3 (hash match) + 0.7 * (2/2) (full key-value overlap) = 1.0
        assert abs(score - 1.0) < 1e-9, f"Expected 1.0, got {score}"

    def test_no_context_returns_zero(self):
        """When memory_context or current_context is None/empty, score is 0.0."""
        ctx = json.dumps({"project": "brainctl"})
        hash_val = "abc123def456abcd"

        # None memory_context
        assert _context_match_score(None, hash_val, ctx, hash_val) == 0.0
        # None current_context
        assert _context_match_score(ctx, hash_val, None, hash_val) == 0.0
        # Both None
        assert _context_match_score(None, None, None, None) == 0.0
        # Empty string memory_context
        assert _context_match_score("", hash_val, ctx, hash_val) == 0.0
        # Empty string current_context
        assert _context_match_score(ctx, hash_val, "", hash_val) == 0.0

    def test_partial_overlap_positive(self):
        """When contexts share some (but not all) key-value pairs, score is positive.

        mem_ctx  = {"project": "brainctl", "agent_id": "agent-1"}
        cur_ctx  = {"project": "brainctl", "agent_id": "agent-2"}
        Overlap: "project" matches, "agent_id" differs.
        Union keys = 2, matching = 1 → overlap ratio = 0.5
        No hash match (different hashes).
        Expected score = 0.7 * 0.5 = 0.35
        """
        mem_ctx = json.dumps({"project": "brainctl", "agent_id": "agent-1"})
        cur_ctx = json.dumps({"project": "brainctl", "agent_id": "agent-2"})
        mem_hash = "aaaa000011112222"
        cur_hash = "bbbb000011112222"

        score = _context_match_score(mem_ctx, mem_hash, cur_ctx, cur_hash)

        expected = 0.7 * 0.5
        assert abs(score - expected) < 1e-9, f"Expected {expected}, got {score}"

    def test_no_overlap_returns_zero(self):
        """When contexts share no key-value pairs and hashes differ, score is 0.0."""
        mem_ctx = json.dumps({"project": "alpha", "agent_id": "agent-1"})
        cur_ctx = json.dumps({"project": "beta", "agent_id": "agent-2"})
        mem_hash = "aaaa000011112222"
        cur_hash = "bbbb333344445555"

        score = _context_match_score(mem_ctx, mem_hash, cur_ctx, cur_hash)

        # 0 matching keys, different hashes → 0.0
        assert score == 0.0, f"Expected 0.0, got {score}"

    def test_score_bounded_0_1(self):
        """Score is always in [0.0, 1.0], even with maximum overlap."""
        ctx = json.dumps({"project": "p", "agent_id": "a", "goal": "g"})
        hash_val = "1234567890abcdef"

        score = _context_match_score(ctx, hash_val, ctx, hash_val)
        assert 0.0 <= score <= 1.0, f"Score {score} out of [0, 1] range"

        # Also check with no overlap — never negative
        ctx_a = json.dumps({"project": "a"})
        ctx_b = json.dumps({"project": "b"})
        score2 = _context_match_score(ctx_a, "aaaa", ctx_b, "bbbb")
        assert 0.0 <= score2 <= 1.0, f"Score {score2} out of [0, 1] range"
