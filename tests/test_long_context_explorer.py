from __future__ import annotations

from pathlib import Path

from agentmemory.retrieval.long_context import analyze_long_context
from agentmemory.retrieval.query_planner import plan_query
from agentmemory.retrieval.second_stage import SecondStageConfig, rerank_top_candidates

from tests.test_second_stage_reranker import _temp_model


def test_long_context_probe_finds_session_anchor(monkeypatch):
    monkeypatch.setenv("BRAINCTL_LONG_CONTEXT_PROBES", "1")
    plan = plan_query("When did Caroline go to the LGBTQ support group?", requested_tables=["memories"])
    candidate = {
        "id": 1,
        "bucket": "memories",
        "type": "memory",
        "final_score": 0.72,
        "retrieval_score": 0.72,
        "source": "keyword",
    }
    text = "\n".join(
        [
            "Session ID: session_1",
            "Session Date: 2025-01-12",
            "Conversation:",
            'Alice: We talked about cooking classes and weekend plans.',
            'Bob: Nothing else noteworthy happened this week.',
            'Caroline: I went to the LGBTQ support group after work and felt better.',
            'Alice: We also mentioned a grocery list and cleaning supplies.',
        ]
    )

    result = analyze_long_context(
        "When did Caroline go to the LGBTQ support group?",
        plan,
        candidate,
        text=text,
    )

    assert result["applicable"] is True
    assert result["score"] > 0.55
    assert result["confidence"] > 0.45
    assert result["uncertainty"] < 0.7
    assert "LGBTQ support group" in result["top_chunk_excerpt"]


def test_second_stage_uses_long_context_probe_to_promote_focused_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAINCTL_LONG_CONTEXT_PROBES", "1")
    plan = plan_query("When did Caroline go to the LGBTQ support group?", requested_tables=["memories"])
    model_path = _temp_model(tmp_path / "tiny.json")
    diffuse = {
        "id": 1,
        "bucket": "memories",
        "type": "memory",
        "content": "\n".join(
            [
                "Session ID: session_7",
                "Session Date: 2025-01-20",
                "Conversation:",
                'Alice: Caroline mentioned some errands after work.',
                'Bob: She later mentioned a support group but I do not remember when.',
                'Alice: Then we switched topics to a restaurant review and sprint planning.',
                'Bob: We also talked about a support group again in passing.',
                'Alice: Nothing pinned the exact date.',
            ]
        ),
        "final_score": 0.84,
        "retrieval_score": 0.84,
        "source": "both",
        "confidence": 0.9,
    }
    focused = {
        "id": 2,
        "bucket": "memories",
        "type": "memory",
        "content": "\n".join(
            [
                "Session ID: session_1",
                "Session Date: 2025-01-12",
                "Conversation:",
                'Caroline: I went to the LGBTQ support group after work on January 12.',
                'Alice: We noted it in the session log for follow-up.',
            ]
        ),
        "final_score": 0.76,
        "retrieval_score": 0.76,
        "source": "keyword",
        "confidence": 0.9,
    }

    reranked, debug = rerank_top_candidates(
        "When did Caroline go to the LGBTQ support group?",
        plan,
        [diffuse, focused],
        config=SecondStageConfig(top_n=2, model_path=str(model_path)),
    )

    assert reranked[0]["id"] == 2
    assert reranked[0]["second_stage_features"]["long_context_score"] > reranked[1]["second_stage_features"]["long_context_score"]
    assert debug["enabled"] is True
