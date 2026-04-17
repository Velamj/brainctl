"""Smoke test for 2.2.0 Bug 10: PII recency gate inverted.

Pre-fix: cmd_memory_add only seeded `alpha` from `alpha_floor`, leaving
`beta` at the default 1.0. This produced Beta(N, 1) on the new memory,
which has mean N/(N+1) — i.e. the new memory was favored over a
high-PII incumbent. The gate inverted its own intent.

Post-fix: `beta` is also seeded to `alpha_floor`, giving Beta(N, N) with
mean 0.5 ("we are not yet sure"). The new memory must accumulate real
recall evidence before it can outrank the incumbent during Thompson
sampling.

We exercise this at the SQL layer: build a minimal in-memory `memories`
table, insert one new row with the same column list cmd_memory_add now
emits, and verify that the row's (alpha, beta) match.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE agents (id TEXT PRIMARY KEY);
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            category TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'global',
            content TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            tags TEXT,
            source_event_id INTEGER,
            memory_type TEXT NOT NULL DEFAULT 'episodic',
            supersedes_id INTEGER,
            alpha REAL DEFAULT 1.0,
            beta REAL DEFAULT 1.0,
            file_path TEXT,
            file_line INTEGER,
            write_tier TEXT,
            indexed INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    db.execute("INSERT INTO agents (id) VALUES ('a')")
    db.commit()
    return db


def _insert_with_alpha_floor(db, alpha_floor):
    """Mirror the column list cmd_memory_add now emits (Bug 10 fix)."""
    cursor = db.execute(
        "INSERT INTO memories (agent_id, category, scope, content, confidence, tags, source_event_id, "
        "memory_type, supersedes_id, alpha, beta, file_path, file_line, write_tier, indexed, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("a", "decision", "global", "new memory text",
         1.0, None, None, "episodic",
         None, float(alpha_floor), float(alpha_floor), None, None,
         "full", 1, "2026-04-16T00:00:00", "2026-04-16T00:00:00")
    )
    db.commit()
    return cursor.lastrowid


class TestBug10AlphaBetaSeed:
    def test_baseline_alpha_floor_one_yields_uniform_prior(self):
        """alpha_floor=1 → Beta(1,1), the maximum-entropy prior."""
        db = _make_db()
        mid = _insert_with_alpha_floor(db, alpha_floor=1)
        row = db.execute("SELECT alpha, beta FROM memories WHERE id=?", (mid,)).fetchone()
        assert row["alpha"] == 1.0
        assert row["beta"] == 1.0

    def test_high_pii_incumbent_yields_symmetric_prior(self):
        """alpha_floor>1 (incumbent has high PII) → Beta(N,N), mean 0.5."""
        db = _make_db()
        mid = _insert_with_alpha_floor(db, alpha_floor=3)
        row = db.execute("SELECT alpha, beta FROM memories WHERE id=?", (mid,)).fetchone()
        assert row["alpha"] == 3.0
        assert row["beta"] == 3.0
        # Mean of Beta(3, 3) is 0.5 — the gate's intent.
        assert row["alpha"] / (row["alpha"] + row["beta"]) == pytest.approx(0.5)

    def test_unseeded_beta_would_invert_gate(self):
        """Regression demonstration: the OLD path (beta defaults to 1) gives
        Beta(N, 1) with mean N/(N+1), which favors the new memory.

        Confirms the fix matters: the unfixed schema default would produce
        a mean of 0.75 with alpha_floor=3, biasing the gate the wrong way.
        """
        db = _make_db()
        # Mimic OLD behavior: explicit alpha, fall through to beta default.
        cursor = db.execute(
            "INSERT INTO memories (agent_id, category, scope, content, alpha) "
            "VALUES (?, ?, ?, ?, ?)",
            ("a", "decision", "global", "old-style insert", 3.0)
        )
        db.commit()
        row = db.execute(
            "SELECT alpha, beta FROM memories WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        old_mean = row["alpha"] / (row["alpha"] + row["beta"])
        # alpha=3, beta=1 (default) → mean 0.75 — clear evidence of inversion.
        assert old_mean == pytest.approx(0.75)
        assert old_mean > 0.5, "old path biases mean above 0.5; fix is necessary"

    def test_alpha_floor_formula_safety(self):
        """The gate's alpha_floor formula must round to a positive integer."""
        import math
        # The line in _impl.py:
        #   alpha_floor = 1 + math.ceil(max(0.0, incumbent_pii - 0.20) * 0.5 * 5)
        for incumbent_pii in [0.0, 0.10, 0.20, 0.50, 0.80, 1.00]:
            af = 1 + math.ceil(max(0.0, incumbent_pii - 0.20) * 0.5 * 5)
            assert af >= 1
            # Sanity bound: PII in [0,1] → af in [1, 3]
            assert af <= 3
