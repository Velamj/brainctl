"""Smoke test for 2.2.0 Bug 5: RRF determinism + id-vs-rowid invariant.

Pre-fix: _rrf_fuse sorted purely by score, so two rows with identical RRF
scores could appear in either order across runs (dict iteration order
leaks into search output).

Post-fix: secondary key on `id` ascending guarantees stable ordering.

The id-vs-rowid concern flagged in the audit is moot at the fusion layer
because _vec_memories upstream re-fetches by id from the base memories
table. We document that fact in the docstring — this test confirms the
upstream contract by feeding the function dict-like rows keyed by id.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _rrf_fuse


class TestRRFTieBreaker:
    def test_identical_scores_produce_stable_id_ascending_order(self):
        """Two rows with identical scores must always sort by id ascending.

        We cross the ranks: id=9 is rank 0 in fts and rank 1 in vec, id=2
        is rank 1 in fts and rank 0 in vec. Each row accumulates the same
        sum 1/(k+1) + 1/(k+2) = identical RRF score. Without a tie-breaker,
        output order is whichever id Python's dict happened to insert
        first (id=9 here, since fts is processed before vec) — varies
        across runs in general. With tie-breaker, lower id always wins.
        """
        fts = [{"id": 9, "content": "x"}, {"id": 2, "content": "y"}]
        vec = [{"id": 2, "content": "y"}, {"id": 9, "content": "x"}]
        out = _rrf_fuse(fts, vec)
        # rrf_score must indeed be tied
        assert out[0]["rrf_score"] == out[1]["rrf_score"], (
            "test setup failed to produce a tie"
        )
        assert out[0]["id"] == 2  # tied score, lower id first
        assert out[1]["id"] == 9

    def test_strict_score_order_dominates_tie_breaker(self):
        """When scores actually differ, the higher score still wins."""
        # Row id=1 only in fts at rank 0; row id=99 in both at rank 0
        # → id=99 has 2x the score and must appear first.
        fts = [{"id": 99, "content": "a"}, {"id": 1, "content": "b"}]
        vec = [{"id": 99, "content": "a"}]
        out = _rrf_fuse(fts, vec)
        assert out[0]["id"] == 99  # higher score
        assert out[1]["id"] == 1

    def test_empty_vec_list_yields_keyword_only(self):
        """Empty vec list — fts list passes through, all flagged keyword."""
        fts = [{"id": 1, "content": "a"}, {"id": 2, "content": "b"}]
        out = _rrf_fuse(fts, [])
        assert len(out) == 2
        assert all(r["source"] == "keyword" for r in out)
        # Sort still respects rank: id=1 was rank 0 → higher RRF score → first
        assert out[0]["id"] == 1

    def test_empty_fts_list_yields_semantic_only(self):
        vec = [{"id": 5, "content": "a"}, {"id": 6, "content": "b"}]
        out = _rrf_fuse([], vec)
        assert len(out) == 2
        assert all(r["source"] == "semantic" for r in out)
        assert out[0]["id"] == 5

    def test_both_empty_yields_empty(self):
        assert _rrf_fuse([], []) == []

    def test_overlap_marks_both_source(self):
        fts = [{"id": 1, "content": "a"}]
        vec = [{"id": 1, "content": "a"}]
        out = _rrf_fuse(fts, vec)
        assert len(out) == 1
        assert out[0]["source"] == "both"

    def test_repeated_invocations_produce_identical_order(self):
        """Determinism check across many calls — output must be byte-stable."""
        fts = [{"id": 7, "x": 1}, {"id": 3, "x": 2}, {"id": 11, "x": 3}]
        vec = [{"id": 7, "x": 1}, {"id": 11, "x": 3}, {"id": 3, "x": 2}]
        ref_ids = [r["id"] for r in _rrf_fuse(fts, vec)]
        for _ in range(50):
            ids = [r["id"] for r in _rrf_fuse(fts, vec)]
            assert ids == ref_ids, "RRF fusion must be deterministic across runs"
