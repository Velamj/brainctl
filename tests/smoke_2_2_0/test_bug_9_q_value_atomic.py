"""Smoke test for 2.2.0 Bug 9: _update_q_value RMW race.

Pre-fix: SELECT q_value, then UPDATE q_value=?  — two concurrent writers can
both read the same q_old and one update is lost.

Post-fix: a single atomic UPDATE that performs the TD step inline using
SQLite arithmetic.

This test exercises *correctness* of the new statement (no leftover SELECT,
correct math, retired-row no-op) rather than trying to reproduce the race
under threading — that would be flaky.  We additionally assert that the
function never reads the value before writing it (a regression guard that
would catch a future re-introduction of the SELECT-then-UPDATE pattern).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _Q_LEARNING_RATE, _update_q_value


def _make_db(q_value=0.5, retired=False):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL DEFAULT 'm',
            q_value REAL DEFAULT 0.5,
            retired_at TEXT DEFAULT NULL
        );
        """
    )
    db.execute(
        "INSERT INTO memories (content, q_value, retired_at) VALUES (?,?,?)",
        ("m", q_value, "2026-01-01T00:00:00" if retired else None),
    )
    db.commit()
    return db


def _q(db, mid=1):
    return db.execute("SELECT q_value FROM memories WHERE id=?", (mid,)).fetchone()["q_value"]


class TestBug9Atomic:
    def test_positive_update_matches_td_formula(self):
        db = _make_db(q_value=0.5)
        _update_q_value(db, 1, contributed=True)
        # 0.5 + 0.1*(1.0 - 0.5) = 0.55
        assert _q(db) == pytest.approx(0.55, abs=1e-9)

    def test_negative_update_matches_td_formula(self):
        db = _make_db(q_value=0.5)
        _update_q_value(db, 1, contributed=False)
        # 0.5 + 0.1*(0.0 - 0.5) = 0.45
        assert _q(db) == pytest.approx(0.45, abs=1e-9)

    def test_null_q_value_treated_as_half(self):
        db = _make_db(q_value=0.5)
        db.execute("UPDATE memories SET q_value = NULL WHERE id=1")
        db.commit()
        _update_q_value(db, 1, contributed=True)
        # NULL treated as 0.5, expect 0.55
        assert _q(db) == pytest.approx(0.55, abs=1e-9)

    def test_clamped_to_unit_interval(self):
        db = _make_db(q_value=0.99)
        for _ in range(100):
            _update_q_value(db, 1, contributed=True)
        assert 0.0 <= _q(db) <= 1.0

        db = _make_db(q_value=0.01)
        for _ in range(100):
            _update_q_value(db, 1, contributed=False)
        assert 0.0 <= _q(db) <= 1.0

    def test_retired_memory_is_noop(self):
        db = _make_db(q_value=0.5, retired=True)
        _update_q_value(db, 1, contributed=True)
        # Unchanged because retired_at IS NOT NULL
        assert _q(db) == pytest.approx(0.5, abs=1e-9)

    def test_missing_memory_is_noop(self):
        db = _make_db(q_value=0.5)
        # No exception, no row affected
        _update_q_value(db, 99999, contributed=True)
        assert _q(db, 1) == pytest.approx(0.5, abs=1e-9)

    def test_atomic_no_select_then_update(self):
        """Regression guard: count SQL statements per call.

        The new implementation should issue exactly one UPDATE per call
        (plus the implicit COMMIT). A re-introduced SELECT-then-UPDATE
        would issue two.
        """
        db = _make_db(q_value=0.5)
        seen = []
        db.set_trace_callback(seen.append)
        _update_q_value(db, 1, contributed=True)
        # We only care about non-empty SQL fragments.
        upd = [s for s in seen if "UPDATE MEMORIES" in s.upper()]
        sel = [s for s in seen if s.strip().upper().startswith("SELECT")]
        assert len(upd) == 1, f"Expected exactly one UPDATE; got {upd}"
        assert sel == [], f"Expected zero SELECTs; got {sel}"
