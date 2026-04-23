"""Debug payload builders for retrieval executive output."""

from __future__ import annotations

from typing import Any


def build_debug_payload(
    *,
    query_plan: dict[str, Any],
    procedure_debug: dict[str, Any] | None,
    answerability: dict[str, Any] | None,
    top_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query_plan": query_plan,
    }
    if procedure_debug:
        payload["procedures"] = procedure_debug
    if answerability:
        payload["answerability"] = answerability
    if top_candidates is not None:
        payload["top_candidates"] = [
            {
                "type": cand.get("type"),
                "id": cand.get("id"),
                "final_score": cand.get("final_score"),
                "why_retrieved": cand.get("why_retrieved"),
                "text": cand.get("content") or cand.get("summary") or cand.get("title") or cand.get("goal") or cand.get("name"),
            }
            for cand in top_candidates[:5]
        ]
    return payload
