"""Tests for staged top-heavy retrieval rollout controls (I6)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from agentmemory._impl import _resolve_topheavy_rollout


def _args(**overrides):
    base = {
        "rollout_mode": None,
        "rollout_canary_agents": None,
        "rollout_canary_percent": None,
        "rollback_top_heavy": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_rollout_default_on_without_env_overrides():
    with patch.dict("os.environ", {}, clear=True):
        out = _resolve_topheavy_rollout(_args(), query="q", agent_id="agent-x")
    assert out["enabled"] is True
    assert out["mode"] == "on"
    assert out["reason"] == "rollout_mode_on"


def test_rollout_off_from_env():
    with patch.dict("os.environ", {"BRAINCTL_TOPHEAVY_ROLLOUT_MODE": "off"}, clear=True):
        out = _resolve_topheavy_rollout(_args(), query="q", agent_id="agent-x")
    assert out["enabled"] is False
    assert out["mode"] == "off"
    assert out["reason"] == "rollout_mode_off"


def test_rollout_canary_allowlist_hit():
    args = _args(rollout_mode="canary", rollout_canary_agents="agent-a, agent-b")
    with patch.dict("os.environ", {}, clear=True):
        out = _resolve_topheavy_rollout(args, query="q", agent_id="agent-b")
    assert out["enabled"] is True
    assert out["mode"] == "canary"
    assert out["canary_hit"] is True
    assert out["reason"] == "canary_agent_allowlist"


def test_rollout_canary_percent_zero_miss():
    args = _args(rollout_mode="canary", rollout_canary_percent=0.0)
    with patch.dict("os.environ", {}, clear=True):
        out = _resolve_topheavy_rollout(args, query="q", agent_id="agent-z")
    assert out["enabled"] is False
    assert out["mode"] == "canary"
    assert out["canary_hit"] is False
    assert "canary_percent_miss_" in out["reason"]


def test_rollout_rollback_switch_wins_over_canary():
    args = _args(rollout_mode="canary", rollout_canary_percent=100.0, rollback_top_heavy=True)
    with patch.dict("os.environ", {}, clear=True):
        out = _resolve_topheavy_rollout(args, query="q", agent_id="agent-z")
    assert out["enabled"] is False
    assert out["mode"] == "off"
    assert out["reason"] == "rollback_forced"
