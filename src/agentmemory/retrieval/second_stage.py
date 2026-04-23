"""Shared second-stage reranking across retrieval buckets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from agentmemory.retrieval.feature_builder import (
    FEATURE_VERSION_V1,
    build_features,
    vectorize_features,
)
from agentmemory.retrieval.judge import JudgeConfig, judge_candidates
from agentmemory.retrieval.mlp_reranker import DEFAULT_MODEL_PATH, TinyMLPModel

_BUCKET_TYPE_MAP = {
    "procedures": "procedure",
    "memories": "memory",
    "events": "event",
    "context": "context",
    "entities": "entity",
    "decisions": "decision",
}


@dataclass(slots=True)
class SecondStageCandidate:
    bucket: str
    original_index: int
    row: dict[str, Any]


@dataclass(slots=True)
class SecondStageConfig:
    enabled: bool = True
    top_n: int = 10
    heuristic_weight: float = 0.62
    mlp_weight: float = 0.28
    judge_weight: float = 0.10
    model_path: str | None = None
    model_enabled: bool = True
    judge: JudgeConfig = field(default_factory=JudgeConfig)

    @classmethod
    def from_args(cls, args: Any) -> "SecondStageConfig":
        judge_enabled = bool(getattr(args, "judge_rerank", None))
        judge_provider = str(getattr(args, "judge_rerank", "ollama") or "ollama")
        judge_model = str(getattr(args, "judge_model", "llama3.2:3b") or "llama3.2:3b")
        top_n = getattr(args, "second_stage_top_n", None)
        if top_n is None:
            try:
                top_n = int(os.environ.get("BRAINCTL_SECOND_STAGE_TOP_N", "10"))
            except (TypeError, ValueError):
                top_n = 10
        return cls(
            enabled=not bool(getattr(args, "no_second_stage", False)) and not bool(getattr(args, "benchmark", False)),
            top_n=max(int(top_n or 10), 1),
            model_enabled=not bool(getattr(args, "no_second_stage_model", False)),
            model_path=getattr(args, "second_stage_model_path", None),
            judge=JudgeConfig(
                enabled=judge_enabled,
                provider=judge_provider,
                model=judge_model,
                top_k=max(min(int(getattr(args, "judge_top_k", 5) or 5), 5), 1),
            ),
        )


def _heuristic_score(plan: Any, features: dict[str, float]) -> float:
    intent = str(getattr(plan, "normalized_intent", "factual") or "factual")
    score = (
        features["base_score"] * 0.24
        + features["informative_overlap"] * 0.23
        + features["tfidf_cosine"] * 0.20
        + features["query_overlap"] * 0.07
        + features["intent_bucket_fit"] * 0.08
        + features["entity_overlap"] * 0.06
        + features["alias_overlap"] * 0.04
        + features["exact_phrase"] * 0.05
        + features["support_evidence_score"] * 0.03
    )
    long_context_reliable = (
        features.get("long_context_applicable", 0.0) > 0.0
        and features.get("long_context_focused_program", 0.0) > 0.0
        and features.get("long_context_confidence", 0.0) >= 0.62
        and features.get("long_context_uncertainty", 0.0) <= 0.38
    )
    if long_context_reliable:
        score += (
            features.get("long_context_score", 0.0) * 0.09
            + features.get("long_context_confidence", 0.0) * 0.03
            + features.get("long_context_agreement", 0.0) * 0.02
            + features.get("long_context_coverage", 0.0) * 0.03
            + features.get("long_context_precision", 0.0) * 0.02
        )
    if features["query_temporal"] > 0:
        score += (
            features["candidate_temporal"] * 0.04
            + features["temporal_anchor_overlap"] * 0.08
            + features["session_gap_score"] * 0.06
        )
        if long_context_reliable:
            score += features.get("long_context_score", 0.0) * 0.05
    if intent in {"temporal", "decision"}:
        score += features["bucket_events"] * 0.04 + features["bucket_decisions"] * 0.03
    if intent in {"procedural", "troubleshooting"}:
        score += features["bucket_procedures"] * 0.06 + features["procedural_candidate"] * 0.04
        if long_context_reliable:
            score += features.get("long_context_confidence", 0.0) * 0.04
    if intent == "factual":
        score += features["bucket_memories"] * 0.05 + features["bucket_entities"] * 0.04
        score -= features["bucket_procedures"] * 0.04
        if long_context_reliable:
            score += features.get("long_context_precision", 0.0) * 0.04
    if features["source_graph"] > 0:
        score -= 0.08
    if features["status_stale"] > 0:
        score -= 0.12
    if features["status_needs_review"] > 0:
        score -= 0.08
    return max(min(score, 1.0), 0.0)


def rerank_top_candidates(
    query: str,
    plan: Any,
    candidates: list[dict[str, Any]],
    config: SecondStageConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rerank a flat candidate list using heuristic + tiny MLP + optional judge."""

    cfg = config or SecondStageConfig()
    if not cfg.enabled or not candidates:
        return candidates, {"enabled": False}

    head = [dict(candidate) for candidate in candidates[: cfg.top_n]]
    tail = [dict(candidate) for candidate in candidates[cfg.top_n :]]
    for idx, candidate in enumerate(head):
        candidate["_stage_position"] = idx
        candidate.setdefault("bucket", candidate.get("type") or "memories")

    feature_rows: list[dict[str, float]] = []
    for idx, candidate in enumerate(head):
        prev_score = head[idx - 1].get("final_score") if idx > 0 else None
        next_score = head[idx + 1].get("final_score") if idx + 1 < len(head) else None
        features = build_features(
            query,
            plan,
            candidate,
            neighbors={"prev_score": prev_score, "next_score": next_score},
        )
        feature_rows.append(features)

    heuristic_scores = [_heuristic_score(plan, features) for features in feature_rows]

    model = TinyMLPModel.try_load(cfg.model_path or DEFAULT_MODEL_PATH) if cfg.model_enabled else None
    if model is not None:
        feature_matrix = [vectorize_features(features, feature_version=FEATURE_VERSION_V1) for features in feature_rows]
        mlp_scores = model.score(feature_matrix)
    else:
        mlp_scores = [0.0] * len(head)

    judge_scores = judge_candidates(query, head, cfg.judge)
    if judge_scores and len(judge_scores) < len(head):
        judge_scores = list(judge_scores) + [0.0] * (len(head) - len(judge_scores))
    elif not judge_scores:
        judge_scores = [0.0] * len(head)

    debug_candidates: list[dict[str, Any]] = []
    for candidate, features, heuristic_score, mlp_score, judge_score in zip(
        head,
        feature_rows,
        heuristic_scores,
        mlp_scores,
        judge_scores,
    ):
        pre_score = float(candidate.get("final_score") or candidate.get("retrieval_score") or 0.0)
        final_score = (
            pre_score * max(0.0, 1.0 - cfg.heuristic_weight - cfg.mlp_weight - cfg.judge_weight)
            + heuristic_score * cfg.heuristic_weight
            + float(mlp_score) * cfg.mlp_weight
            + float(judge_score) * cfg.judge_weight
        )
        candidate["pre_second_stage_score"] = round(pre_score, 8)
        candidate["second_stage_heuristic"] = round(heuristic_score, 6)
        candidate["second_stage_mlp"] = round(float(mlp_score), 6)
        candidate["second_stage_judge"] = round(float(judge_score), 6)
        candidate["second_stage_features"] = {
            key: features[key]
            for key in (
                "informative_overlap",
                "tfidf_cosine",
                "entity_overlap",
                "temporal_anchor_overlap",
                "intent_bucket_fit",
                "session_gap_score",
                "long_context_score",
                "long_context_confidence",
                "long_context_agreement",
                "long_context_uncertainty",
                "long_context_focused_program",
            )
        }
        long_context_debug = candidate.pop("_long_context_debug", None) or {}
        if long_context_debug.get("applicable"):
            candidate["second_stage_features"]["long_context_program"] = long_context_debug.get("program")
            candidate["second_stage_features"]["long_context_excerpt"] = long_context_debug.get("top_chunk_excerpt")
        candidate["final_score"] = round(final_score, 8)
        debug_candidates.append(
            {
                "bucket": candidate.get("bucket"),
                "id": candidate.get("id"),
                "pre_score": round(pre_score, 6),
                "heuristic": round(heuristic_score, 6),
                "mlp": round(float(mlp_score), 6),
                "judge": round(float(judge_score), 6),
                "final": round(final_score, 6),
                "features": candidate["second_stage_features"],
            }
        )

    head.sort(key=lambda item: item.get("final_score", 0.0), reverse=True)
    reranked = head + tail
    debug = {
        "enabled": True,
        "top_n": cfg.top_n,
        "model_enabled": cfg.model_enabled,
        "model_path": str(cfg.model_path or DEFAULT_MODEL_PATH),
        "model_loaded": model is not None,
        "judge_enabled": cfg.judge.enabled,
        "base_weight": round(max(0.0, 1.0 - cfg.heuristic_weight - cfg.mlp_weight - cfg.judge_weight), 4),
        "mlp_weight": round(cfg.mlp_weight, 4),
        "judge_weight": round(cfg.judge_weight, 4),
        "candidates": debug_candidates,
    }
    return reranked, debug


def rerank_bucketed_results(
    query: str,
    plan: Any,
    buckets: dict[str, list[dict[str, Any]]],
    config: SecondStageConfig | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Apply second-stage reranking to the combined head across all buckets."""

    cfg = config or SecondStageConfig()
    if not cfg.enabled:
        return buckets, {"enabled": False}

    ordered: list[SecondStageCandidate] = []
    for bucket_name in ("procedures", "memories", "events", "context", "entities", "decisions"):
        rows = buckets.get(bucket_name) or []
        for idx, row in enumerate(rows):
            candidate = dict(row)
            candidate["bucket"] = bucket_name
            candidate["type"] = str(candidate.get("type") or _BUCKET_TYPE_MAP.get(bucket_name, bucket_name))
            ordered.append(SecondStageCandidate(bucket_name, idx, candidate))
    ordered.sort(key=lambda item: item.row.get("final_score", 0.0), reverse=True)

    reranked_rows, debug = rerank_top_candidates(
        query,
        plan,
        [item.row for item in ordered],
        config=cfg,
    )
    scored: dict[tuple[str, Any], dict[str, Any]] = {}
    for row in reranked_rows:
        scored[(str(row.get("bucket") or "memories"), row.get("id"))] = row

    updated: dict[str, list[dict[str, Any]]] = {name: [] for name in buckets}
    for bucket_name, rows in buckets.items():
        updated_rows: list[dict[str, Any]] = []
        for row in rows or []:
            updated_rows.append(scored.get((bucket_name, row.get("id")), row))
        updated_rows.sort(key=lambda item: item.get("final_score", 0.0), reverse=True)
        updated[bucket_name] = updated_rows
    return updated, debug
