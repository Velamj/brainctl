"""Tests for Task C6: Per-Project Retrieval Presets.

Papers: Finn et al. 2017 / MAML

Covers:
- test_save_load_roundtrip
- test_missing_preset_returns_none
- test_overwrite_updates_value
- test_project_independence
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _load_project_preset, _save_project_preset


# ---------------------------------------------------------------------------
# Minimal in-memory DB with just the agent_state table
# ---------------------------------------------------------------------------

def _make_db():
    """Create a minimal in-memory DB with only the agent_state table."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS agent_state (
            agent_id TEXT NOT NULL,
            key      TEXT NOT NULL,
            value    TEXT,
            updated_at TEXT,
            PRIMARY KEY (agent_id, key)
        );
    """)
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSaveLoadRoundtrip:

    def test_save_load_roundtrip(self):
        """A preset saved for an agent+project is returned unchanged by load."""
        db = _make_db()
        preset = {"recency_weight": 0.4, "fts_weight": 0.6, "vector_weight": 0.0}

        _save_project_preset(db, "agent-a", "brainctl", preset)
        loaded = _load_project_preset(db, "agent-a", "brainctl")

        assert loaded == preset

    def test_missing_preset_returns_none(self):
        """Loading a preset that was never saved returns None."""
        db = _make_db()
        result = _load_project_preset(db, "agent-a", "nonexistent-project")
        assert result is None

    def test_overwrite_updates_value(self):
        """Saving a second preset for the same agent+project overwrites the first."""
        db = _make_db()
        first_preset = {"recency_weight": 0.3, "fts_weight": 0.7}
        second_preset = {"recency_weight": 0.8, "fts_weight": 0.2}

        _save_project_preset(db, "agent-b", "project-x", first_preset)
        _save_project_preset(db, "agent-b", "project-x", second_preset)
        loaded = _load_project_preset(db, "agent-b", "project-x")

        assert loaded == second_preset
        # Verify the first preset is gone
        assert loaded != first_preset

        # Also confirm only one row exists for this agent+key
        rows = db.execute(
            "SELECT COUNT(*) AS cnt FROM agent_state WHERE agent_id=? AND key=?",
            ("agent-b", "retrieval_preset:project-x"),
        ).fetchone()
        assert rows["cnt"] == 1

    def test_project_independence(self):
        """Presets for different projects under the same agent are independent."""
        db = _make_db()
        preset_alpha = {"recency_weight": 0.1, "fts_weight": 0.9}
        preset_beta = {"recency_weight": 0.9, "fts_weight": 0.1}

        _save_project_preset(db, "agent-c", "project-alpha", preset_alpha)
        _save_project_preset(db, "agent-c", "project-beta", preset_beta)

        loaded_alpha = _load_project_preset(db, "agent-c", "project-alpha")
        loaded_beta = _load_project_preset(db, "agent-c", "project-beta")

        assert loaded_alpha == preset_alpha
        assert loaded_beta == preset_beta
        # Ensure cross-contamination did not occur
        assert loaded_alpha != loaded_beta
