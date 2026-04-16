"""Tests for encoding context snapshot (Task C1, Migration 040).

Papers: Tulving & Thomson 1973, Heald et al. 2023, Pink et al. 2025

Covers:
- test_build_encoding_context_full
- test_build_encoding_context_partial
- test_build_encoding_context_empty
- test_encoding_context_hash_deterministic
- test_encoding_context_hash_unique
- test_encoding_context_hash_format
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory._impl import _build_encoding_context, _encoding_context_hash


# ---------------------------------------------------------------------------
# _build_encoding_context tests
# ---------------------------------------------------------------------------

class TestBuildEncodingContext:
    def test_full_context_contains_all_fields(self):
        """All provided fields appear in the JSON output."""
        result = _build_encoding_context(
            project="brainctl",
            agent_id="test-agent",
            session_id="sess-abc123",
            goal="implement migration",
            active_tool="memory_add",
        )
        data = json.loads(result)
        assert data["project"] == "brainctl"
        assert data["agent_id"] == "test-agent"
        assert data["session_id"] == "sess-abc123"
        assert data["goal"] == "implement migration"
        assert data["active_tool"] == "memory_add"

    def test_partial_context_omits_none_fields(self):
        """Fields not provided (None) are omitted from the JSON output."""
        result = _build_encoding_context(project="myproject", agent_id="agent-1")
        data = json.loads(result)
        assert "project" in data
        assert "agent_id" in data
        assert "session_id" not in data
        assert "goal" not in data
        assert "active_tool" not in data

    def test_empty_context_returns_empty_json_object(self):
        """With all None args, returns '{}' (an empty JSON object, not empty string)."""
        result = _build_encoding_context()
        assert result == "{}"
        assert json.loads(result) == {}

    def test_output_is_valid_json(self):
        """Output is always parseable JSON."""
        for project in ("myproj", None):
            for agent_id in ("agent-x", None):
                result = _build_encoding_context(project=project, agent_id=agent_id)
                parsed = json.loads(result)
                assert isinstance(parsed, dict)

    def test_output_keys_are_sorted(self):
        """Keys are sorted (sort_keys=True) — ensures deterministic ordering."""
        result = _build_encoding_context(
            project="z-proj",
            agent_id="a-agent",
            session_id="s-sess",
        )
        data = json.loads(result)
        keys = list(data.keys())
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# _encoding_context_hash tests
# ---------------------------------------------------------------------------

class TestEncodingContextHash:
    def test_hash_is_deterministic(self):
        """Same inputs always produce the same hash."""
        h1 = _encoding_context_hash(project="brainctl", agent_id="agent-1", session_id="sess-1")
        h2 = _encoding_context_hash(project="brainctl", agent_id="agent-1", session_id="sess-1")
        assert h1 == h2

    def test_different_projects_produce_different_hashes(self):
        """Different project values produce different hashes."""
        h1 = _encoding_context_hash(project="proj-a", agent_id="agent-1")
        h2 = _encoding_context_hash(project="proj-b", agent_id="agent-1")
        assert h1 != h2

    def test_different_agents_produce_different_hashes(self):
        """Different agent_id values produce different hashes."""
        h1 = _encoding_context_hash(project="proj", agent_id="agent-1")
        h2 = _encoding_context_hash(project="proj", agent_id="agent-2")
        assert h1 != h2

    def test_hash_format_is_16_hex_chars(self):
        """Hash is exactly 16 lowercase hex characters."""
        h = _encoding_context_hash(project="brainctl", agent_id="test-agent", session_id="abc")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_matches_sha256_prefix(self):
        """Hash equals first 16 hex chars of SHA-256('project:agent:session')."""
        project, agent_id, session_id = "brainctl", "my-agent", "sess-xyz"
        expected_key = f"{project}:{agent_id}:{session_id}"
        expected = hashlib.sha256(expected_key.encode()).hexdigest()[:16]
        assert _encoding_context_hash(project=project, agent_id=agent_id, session_id=session_id) == expected

    def test_none_args_produce_stable_hash(self):
        """All-None args produce the hash of '::' without raising."""
        h = _encoding_context_hash()
        expected = hashlib.sha256(b"::").hexdigest()[:16]
        assert h == expected
