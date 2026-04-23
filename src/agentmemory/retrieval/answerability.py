"""Grounded answerability gate."""

from __future__ import annotations

import re
from typing import Any

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "has", "have", "how", "i", "in", "is", "it", "its", "of",
    "on", "or", "that", "the", "to", "was", "we", "what", "when", "where",
    "which", "who", "why", "will", "with", "you", "did",
}


def _normalize_token(token: str) -> str:
    tok = re.sub(r"[^a-z0-9]+", "", (token or "").lower())
    if len(tok) <= 2 or tok in _STOPWORDS:
        return ""
    if tok.endswith("ies") and len(tok) > 4:
        tok = tok[:-3] + "y"
    elif tok.endswith("ed") and len(tok) > 4:
        tok = tok[:-2]
    elif tok.endswith("es") and len(tok) > 4:
        tok = tok[:-2]
    elif tok.endswith("s") and len(tok) > 3:
        tok = tok[:-1]
    return tok


def _token_set(text: str) -> set[str]:
    return {
        norm
        for part in re.split(r"\s+", text or "")
        if (norm := _normalize_token(part))
    }


def assess_answerability(
    query: str,
    plan,
    buckets: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Estimate whether the current retrieval set is grounded enough to answer."""

    flat: list[dict[str, Any]] = []
    for rows in buckets.values():
        flat.extend(rows or [])
    flat.sort(key=lambda item: item.get("final_score", 0.0), reverse=True)

    if not flat:
        return {
            "score": 0.0,
            "abstain": True,
            "reason": "no_candidates",
            "top_margin": 0.0,
        }

    top = flat[0]
    second = flat[1] if len(flat) > 1 else None
    top_score = float(top.get("final_score") or 0.0)
    second_score = float(second.get("final_score") or 0.0) if second else 0.0
    margin = top_score - second_score

    query_tokens = _token_set(query)
    top_text = " ".join(
        str(top.get(key) or "")
        for key in ("content", "summary", "title", "goal", "description", "search_text")
    )
    top_text_tokens = _token_set(top_text)
    supporting_text = " ".join(
        " ".join(
            str(row.get(key) or "")
            for key in ("content", "summary", "title", "goal", "description", "search_text")
        )
        for row in flat[:3]
    )
    supporting_tokens = _token_set(supporting_text)
    coverage = 0.0
    if query_tokens:
        coverage = len(query_tokens & supporting_tokens) / len(query_tokens)
    anchor_overlap = len(query_tokens & top_text_tokens)
    evidence_diversity = len({
        row.get("type") or bucket_name.rstrip("s")
        for bucket_name, rows in buckets.items()
        for row in (rows or [])[:2]
    })
    direct_support = len(top.get("supporting_evidence") or [])
    stale_penalty = 0.25 if top.get("status") in {"stale", "needs_review", "superseded", "retired"} else 0.0

    score = (
        (top_score * 0.45)
        + (margin * 0.35)
        + (coverage * 0.45)
        + min(direct_support / 3.0, 1.0) * 0.15
        + min(evidence_diversity / 3.0, 1.0) * 0.1
        - stale_penalty
    )
    abstain = False
    reason = "grounded"
    if coverage < 0.34 and anchor_overlap == 0 and direct_support == 0:
        abstain = True
        reason = "weak_token_coverage"
    if margin < 0.08 and coverage < 0.5 and anchor_overlap < 2 and plan.abstain_allowed:
        abstain = True
        reason = "diffuse_candidates"
    if plan.abstain_allowed and score < 0.42 and coverage < 0.5:
        abstain = True
        reason = "low_answerability_score"

    return {
        "score": round(score, 4),
        "abstain": abstain,
        "reason": reason,
        "top_margin": round(margin, 4),
        "coverage": round(coverage, 4),
        "anchor_overlap": anchor_overlap,
        "evidence_diversity": evidence_diversity,
        "direct_support": direct_support,
    }
