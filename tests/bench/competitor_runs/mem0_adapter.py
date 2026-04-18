"""Mem0 adapter — uses the hosted Mem0 Cloud API (mem0ai SDK).

Pinned to mem0ai==2.0.0 (PyPI 2026-04-16). Mem0 docs:
https://docs.mem0.ai/

How Mem0 stores: ``client.add(messages=[...], user_id=...)`` runs an
LLM-backed "fact extraction" pass that turns each message into one or
more "memories" (extracted statements). To make scoring honest:

  * we DISABLE auto-fact-extraction by passing ``infer=False`` so each
    add() persists the literal text 1:1 — this preserves the
    ``[key=...]`` marker the gold matcher needs.
  * the per-conversation tenant is namespaced by ``user_id="brainctl-bench-<tenant>"``
    so we can wipe via ``client.delete_all(user_id=...)`` in teardown.

Cost (Mem0 Cloud, April 2026 published rates):
  * adds:    $0.10 per 1k operations  (LLM extraction is "free" on
             infer=False per their docs but billed as raw write)
  * search:  $0.05 per 1k queries
LOCOMO has 5882 turns + 1982 questions =>
  full: 5882 writes + 1982 queries * 2 runs ≈
  ($0.59 + $0.10) * 2 = ~$1.40. Within the $5 budget.
LongMemEval _s ranges 16k-200k+ sessions per entry (each entry is a
fresh tenant). Full sweep is wildly out of budget (~$50+) — use
``--limit 50`` smoke instead.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .common import (
    CompetitorUnavailable,
    short_text_for,
    require_env,
    wrap_result,
)


class Mem0Adapter:
    name = "mem0"
    pinned_version = "2.0.0"
    needs_api_key = True
    cost_per_1k_writes_usd = 0.10
    cost_per_1k_queries_usd = 0.05

    def __init__(self) -> None:
        try:
            from mem0 import MemoryClient  # type: ignore
        except ImportError as exc:
            raise CompetitorUnavailable(
                self.name,
                f"mem0ai SDK not installed (pip install mem0ai=={self.pinned_version}): {exc!r}",
            ) from exc
        api_key = require_env("MEM0_API_KEY", self.name)
        try:
            self._client = MemoryClient(api_key=api_key)
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"MemoryClient init failed: {exc!r}"
            ) from exc
        self._user_id: str = ""

    def setup(self, tenant_id: str) -> None:
        self._user_id = f"brainctl-bench-{tenant_id}"

    def ingest(
        self,
        key: str,
        text: str,
        speaker: str = "",
        timestamp: str = "",
    ) -> None:
        body = short_text_for(key, text, speaker, timestamp)
        # infer=False is the key honesty knob: with infer=True (Mem0
        # default) the SDK calls an LLM to extract "facts" and stores
        # those instead of the raw text — which would strip our
        # [key=...] marker and silently zero out the score.
        try:
            self._client.add(
                messages=[{"role": speaker or "user", "content": body}],
                user_id=self._user_id,
                infer=False,
            )
        except Exception as exc:  # noqa: BLE001
            # Per-call failures bubble out so the runner records them.
            raise CompetitorUnavailable(
                self.name, f"add failed: {exc!r}"
            ) from exc

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        try:
            hits = self._client.search(
                query=query,
                user_id=self._user_id,
                limit=top_k,
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"search failed: {exc!r}"
            ) from exc
        # Mem0 returns either a list[dict] or {"results": [...]} depending
        # on SDK version; normalise both.
        if isinstance(hits, dict) and "results" in hits:
            hits = hits["results"]
        out: List[Dict[str, Any]] = []
        for h in hits or []:
            text = h.get("memory") or h.get("text") or h.get("content") or ""
            out.append(wrap_result(text, score=h.get("score", 0.0), id=h.get("id")))
        return out

    def teardown(self) -> None:
        if not self._user_id:
            return
        try:
            self._client.delete_all(user_id=self._user_id)
        except Exception:
            # Best-effort cleanup; don't raise during teardown — that
            # would mask the real measurement above.
            pass
