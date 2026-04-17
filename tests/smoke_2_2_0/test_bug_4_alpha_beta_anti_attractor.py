"""Smoke test for 2.2.0 Bug 4: Bayesian alpha/beta decoupled.

Pre-fix: every successful recall incremented alpha by 1.0 but never
touched beta. After 100 recalls the prior reached Beta(101, 1) — mean
~0.99 — and Thompson sampling at retrieval time let popular-but-wrong
memories crowd out less-recalled competitors.

Post-fix (option c, "anti-attractor prior"): each recall increments
alpha by 1.0 AND beta by _BETA_PRIOR_INCREMENT (0.1). Both increments
are gated on (alpha + beta) < _AB_PRIOR_CAP (1000) via a CASE
expression so the whole update is one atomic statement.

What this test guarantees:
  - alpha and beta both rise per recall (decoupling fixed)
  - the alpha:beta ratio is bounded (~10:1 at the recall limit), so
    posterior mean cannot reach 0.99 from sheer popularity
  - the cap fires together for both fields (no skewed runaway past it)
  - the existing confidence/recall-count side-effects are preserved
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agentmemory._impl as impl  # noqa: E402


def _make_db(alpha=1.0, beta=1.0, confidence=0.5):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL DEFAULT 'm',
            confidence REAL NOT NULL DEFAULT 0.5,
            alpha REAL DEFAULT 1.0,
            beta  REAL DEFAULT 1.0,
            recalled_count INTEGER NOT NULL DEFAULT 0,
            last_recalled_at TEXT,
            labile_until TEXT,
            q_value REAL DEFAULT 0.5,
            retired_at TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO memories (id, alpha, beta, confidence) VALUES (1, ?, ?, ?)",
        (alpha, beta, confidence),
    )
    db.commit()
    return db


def _row(db, mid=1):
    return dict(db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone())


class TestBug4AntiAttractor:
    def test_single_recall_increments_both_alpha_and_beta(self):
        db = _make_db(alpha=1.0, beta=1.0)
        impl._retrieval_practice_boost(db, memory_id=1, retrieval_prediction_error=0.0)
        r = _row(db)
        assert r["alpha"] == pytest.approx(2.0)
        assert r["beta"] == pytest.approx(1.0 + impl._BETA_PRIOR_INCREMENT)

    def test_increment_constants_match_module(self):
        """Sanity: the constants exposed at the module top are what the
        update actually uses."""
        assert impl._BETA_PRIOR_INCREMENT == pytest.approx(0.1)
        assert impl._AB_PRIOR_CAP == pytest.approx(1000.0)

    def test_hundred_recalls_bound_posterior_mean_well_below_old_ceiling(self):
        """100 recalls under the new rule stays at mean ~10/11 ≈ 0.91.

        The OLD rule would have produced Beta(101, 1) → mean ≈ 0.99.
        The new rule produces Beta(101, 1 + 100*0.1) = Beta(101, 11) →
        mean ≈ 0.902. We assert the new mean is meaningfully lower than
        what the old rule would have given.
        """
        db = _make_db(alpha=1.0, beta=1.0)
        for _ in range(100):
            impl._retrieval_practice_boost(db, memory_id=1)
        r = _row(db)
        assert r["alpha"] == pytest.approx(101.0)
        assert r["beta"] == pytest.approx(1.0 + 100 * impl._BETA_PRIOR_INCREMENT)
        new_mean = r["alpha"] / (r["alpha"] + r["beta"])
        old_mean = 101.0 / (101.0 + 1.0)
        assert new_mean < 0.92, f"new mean {new_mean} too close to old runaway"
        assert old_mean - new_mean > 0.07, (
            f"new mean ({new_mean:.4f}) must be meaningfully below old "
            f"({old_mean:.4f})"
        )

    def test_cap_freezes_both_fields(self):
        """When (alpha + beta) reaches the cap, NEITHER field grows further."""
        # Set right at the cap. Subsequent recalls must be no-ops on both.
        db = _make_db(alpha=900.0, beta=100.0)  # sum = 1000 = _AB_PRIOR_CAP
        before = _row(db)
        for _ in range(5):
            impl._retrieval_practice_boost(db, memory_id=1)
        after = _row(db)
        assert after["alpha"] == before["alpha"]
        assert after["beta"] == before["beta"]
        # recalled_count still increments — the cap only freezes the prior
        # mass, not the bookkeeping.
        assert after["recalled_count"] == before["recalled_count"] + 5

    def test_cap_does_not_fire_just_below_threshold(self):
        """Just below the cap the next recall should still proceed."""
        # alpha + beta = 999.5 → next increment lands exactly at 1000.6,
        # so the WHEN-clause should still evaluate False (we re-read both
        # fields fresh for the second WHEN, but the predicate uses pre-
        # update values via SET-list semantics — this test confirms it).
        db = _make_db(alpha=900.0, beta=99.5)  # sum = 999.5 < 1000
        impl._retrieval_practice_boost(db, memory_id=1)
        r = _row(db)
        assert r["alpha"] == pytest.approx(901.0)
        assert r["beta"] == pytest.approx(99.5 + impl._BETA_PRIOR_INCREMENT)

    def test_confidence_boost_preserved(self):
        """The testing-effect confidence boost is still applied per recall."""
        db = _make_db(alpha=1.0, beta=1.0, confidence=0.5)
        impl._retrieval_practice_boost(db, memory_id=1, retrieval_prediction_error=0.0)
        r = _row(db)
        # boost = _RETRIEVAL_PRACTICE_BASE_BOOST * (1 + 0) = 0.02
        assert r["confidence"] == pytest.approx(0.5 + impl._RETRIEVAL_PRACTICE_BASE_BOOST)

    def test_higher_rpe_strengthens_more(self):
        """Hard retrievals (high RPE) deliver more confidence boost."""
        db_easy = _make_db(alpha=1.0, beta=1.0, confidence=0.5)
        db_hard = _make_db(alpha=1.0, beta=1.0, confidence=0.5)
        impl._retrieval_practice_boost(db_easy, 1, retrieval_prediction_error=0.0)
        impl._retrieval_practice_boost(db_hard, 1, retrieval_prediction_error=1.0)
        easy_conf = _row(db_easy)["confidence"]
        hard_conf = _row(db_hard)["confidence"]
        # boost(rpe=1) = base * 2; boost(rpe=0) = base * 1
        assert hard_conf > easy_conf

    def test_null_alpha_beta_treated_as_one(self):
        """COALESCE handles legacy rows where alpha or beta is NULL."""
        db = _make_db()
        db.execute("UPDATE memories SET alpha = NULL, beta = NULL WHERE id = 1")
        db.commit()
        impl._retrieval_practice_boost(db, memory_id=1)
        r = _row(db)
        # NULLs treated as 1.0 → alpha=2.0, beta=1.1
        assert r["alpha"] == pytest.approx(2.0)
        assert r["beta"] == pytest.approx(1.0 + impl._BETA_PRIOR_INCREMENT)

    def test_atomic_single_update_to_memories(self):
        """Regression guard: the alpha/beta increment must be a single
        UPDATE so concurrent recalls cannot lose increments."""
        db = _make_db(alpha=1.0, beta=1.0)
        seen = []
        db.set_trace_callback(seen.append)
        impl._retrieval_practice_boost(db, memory_id=1)
        # Filter out the q_value UPDATE (handled by _update_q_value).
        # The retrieval-practice path itself must issue exactly one
        # UPDATE memories statement and zero SELECTs.
        rp_updates = [
            s for s in seen
            if s.strip().upper().startswith("UPDATE MEMORIES")
            and "ALPHA" in s.upper()
        ]
        assert len(rp_updates) == 1, (
            f"retrieval-practice path must issue exactly one alpha/beta "
            f"UPDATE; got {rp_updates}"
        )
