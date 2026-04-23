from __future__ import annotations

import json
from pathlib import Path

from agentmemory.retrieval.judge import JudgeConfig, judge_candidates
from agentmemory.retrieval.mlp_reranker import TinyMLPModel
from agentmemory.retrieval.query_planner import plan_query
from agentmemory.retrieval.second_stage import SecondStageConfig, rerank_bucketed_results, rerank_top_candidates


def _temp_model(path: Path) -> Path:
    payload = {
        "feature_version": "v1",
        "feature_order": [
            "base_score", "retrieval_score", "rrf_score", "confidence", "query_overlap",
            "informative_overlap", "tfidf_cosine", "exact_phrase", "entity_overlap",
            "alias_overlap", "query_temporal", "candidate_temporal", "temporal_anchor_overlap",
            "query_session_hint", "candidate_session_hint", "session_gap_score", "intent_bucket_fit",
            "source_keyword", "source_semantic", "source_both", "source_graph", "bucket_memories",
            "bucket_events", "bucket_entities", "bucket_procedures", "bucket_decisions",
            "candidate_age_score", "support_evidence_score", "status_active", "status_stale",
            "status_needs_review", "position_score", "neighbor_margin", "query_length_score",
            "candidate_length_score", "procedural_candidate",
        ],
        "norm_mean": [0.0] * 36,
        "norm_std": [1.0] * 36,
        "w1": [[0.0] * 36 for _ in range(32)],
        "b1": [0.0] * 32,
        "w2": [[0.0] * 32 for _ in range(16)],
        "b2": [0.0] * 16,
        "w3": [[0.0] * 16],
        "b3": [0.0],
        "metadata": {"test": True},
    }
    # Make one hidden path look at informative overlap and cosine similarity.
    payload["w1"][0][5] = 1.2
    payload["w1"][0][6] = 1.2
    payload["w2"][0][0] = 1.0
    payload["w3"][0][0] = 1.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_tiny_mlp_load_and_score(tmp_path: Path):
    model_path = _temp_model(tmp_path / "tiny.json")
    model = TinyMLPModel.load(model_path)
    scores = model.score(
        [
            [0.0, 0.0, 0.0, 0.0, 0.2, 0.9, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            [0.0] * 36,
        ]
    )
    assert len(scores) == 2
    assert scores[0] > scores[1]


def test_second_stage_promotes_exact_match(tmp_path: Path):
    plan = plan_query("When did Caroline go to the LGBTQ support group?", requested_tables=["memories"])
    model_path = _temp_model(tmp_path / "tiny.json")
    candidates = [
        {
            "id": 1,
            "bucket": "memories",
            "type": "memory",
            "content": "Caroline mentioned a cooking class during session_7.",
            "final_score": 0.85,
            "retrieval_score": 0.85,
            "source": "both",
            "confidence": 0.9,
        },
        {
            "id": 2,
            "bucket": "memories",
            "type": "memory",
            "content": "Session ID: session_1\nCaroline went to the LGBTQ support group on January 12.",
            "final_score": 0.78,
            "retrieval_score": 0.78,
            "source": "keyword",
            "confidence": 0.9,
        },
    ]
    reranked, debug = rerank_top_candidates(
        "When did Caroline go to the LGBTQ support group?",
        plan,
        candidates,
        config=SecondStageConfig(top_n=2, model_path=str(model_path)),
    )
    assert reranked[0]["id"] == 2
    assert reranked[0]["pre_second_stage_score"] == 0.78
    assert debug["enabled"] is True
    assert debug["model_loaded"] is True


def test_bucketed_rerank_preserves_bucket_membership(tmp_path: Path):
    plan = plan_query("How do I roll back a bad release?", requested_tables=["procedures", "memories"])
    model_path = _temp_model(tmp_path / "tiny.json")
    buckets = {
        "procedures": [
            {
                "id": 9,
                "title": "Rollback release",
                "goal": "Restore service after a bad release",
                "final_score": 0.74,
                "retrieval_score": 0.74,
                "source": "procedure_fts",
                "status": "active",
            }
        ],
        "memories": [
            {
                "id": 10,
                "content": "We chose SQLite because it is easy to operate.",
                "final_score": 0.83,
                "retrieval_score": 0.83,
                "source": "both",
            }
        ],
        "events": [],
        "context": [],
        "entities": [],
        "decisions": [],
    }
    updated, _debug = rerank_bucketed_results(
        "How do I roll back a bad release?",
        plan,
        buckets,
        config=SecondStageConfig(top_n=2, model_path=str(model_path)),
    )
    assert updated["procedures"][0]["id"] == 9
    assert "pre_second_stage_score" in updated["procedures"][0]
    assert updated["memories"][0]["id"] == 10


def test_bucketed_rerank_disabled_is_noop():
    plan = plan_query("Who owns the consolidation daemon?", requested_tables=["entities", "memories"])
    buckets = {
        "procedures": [],
        "memories": [
            {
                "id": 21,
                "type": "memory",
                "content": "Bob owns the consolidation daemon and dream cycles.",
                "final_score": 0.83,
            }
        ],
        "events": [],
        "context": [],
        "entities": [
            {
                "id": 2,
                "type": "entity",
                "name": "Bob",
                "final_score": 0.91,
            }
        ],
        "decisions": [],
    }
    updated, debug = rerank_bucketed_results(
        "Who owns the consolidation daemon?",
        plan,
        buckets,
        config=SecondStageConfig(enabled=False),
    )
    assert updated is buckets
    assert updated["entities"][0]["type"] == "entity"
    assert updated["memories"][0]["type"] == "memory"
    assert debug == {"enabled": False}


def test_judge_disabled_returns_empty():
    scores = judge_candidates(
        "What is SQLite?",
        [{"content": "SQLite is an embedded database."}],
        JudgeConfig(enabled=False),
    )
    assert scores == []
