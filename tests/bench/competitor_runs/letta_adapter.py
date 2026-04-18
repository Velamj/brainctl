"""Letta (formerly MemGPT) adapter via the hosted Letta Cloud REST API.

Pinned to letta-client==1.10.3. Letta docs: https://docs.letta.com/

Letta's memory model is split across a small "core memory block"
(persona + human) and a paginated "archival memory" store. We use
**archival memory** for benchmark scoring — that's the long-term
substrate per Letta's own docs; core memory is essentially a system
prompt and not what an external benchmark should lean on.

Lifecycle:
  setup:      client.agents.create(name=tenant_id, model="...")
  ingest:     client.agents.passages.create(agent_id, text=...)
  search:     client.agents.passages.search(agent_id, query=..., limit=k)
  teardown:   client.agents.delete(agent_id)

Letta charges per LLM token used by their default agent loop (chat
+ embedding). For pure archival store + retrieve calls, only the
embedding is billed (their docs: $0.10 per 1M embedding tokens via
text-embedding-3-small). Average LOCOMO turn ~25 tokens =>
5882 writes ≈ 150k tokens ≈ $0.015. 1982 queries ≈ 50k ≈ $0.005. Cheap.

Note: Letta also supports a self-hosted server (docker run letta/letta)
which is FREE but requires Postgres + Ollama + an LLM serving stack.
The cloud path is faster to wire for a comparison sweep.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .common import (
    CompetitorUnavailable,
    require_env,
    short_text_for,
    wrap_result,
)


class LettaAdapter:
    name = "letta"
    pinned_version = "1.10.3"
    needs_api_key = True
    cost_per_1k_writes_usd = 0.0025   # ~25 emb tokens/turn @ $0.10/1M
    cost_per_1k_queries_usd = 0.001   # ~10 emb tokens/query

    def __init__(self) -> None:
        try:
            from letta_client import Letta  # type: ignore
        except ImportError as exc:
            raise CompetitorUnavailable(
                self.name,
                f"letta-client not installed (pip install letta-client=={self.pinned_version}): {exc!r}",
            ) from exc
        api_key = require_env("LETTA_API_KEY", self.name)
        try:
            self._client = Letta(token=api_key)
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"Letta init failed: {exc!r}"
            ) from exc
        self._agent_id: str = ""

    def setup(self, tenant_id: str) -> None:
        # One Letta agent per tenant — keeps archival memory namespaces
        # cleanly isolated. Cheap to create/destroy; archival store
        # itself doesn't bill until the embedder runs.
        try:
            agent = self._client.agents.create(
                name=f"brainctl-bench-{tenant_id}",
                memory_blocks=[],
                model="openai/gpt-4o-mini",
                embedding="openai/text-embedding-3-small",
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"agents.create failed: {exc!r}"
            ) from exc
        self._agent_id = agent.id

    def ingest(
        self,
        key: str,
        text: str,
        speaker: str = "",
        timestamp: str = "",
    ) -> None:
        body = short_text_for(key, text, speaker, timestamp)
        try:
            self._client.agents.passages.create(
                agent_id=self._agent_id, text=body
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"passages.create failed: {exc!r}"
            ) from exc

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        try:
            hits = self._client.agents.passages.search(
                agent_id=self._agent_id, query=query, limit=top_k
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"passages.search failed: {exc!r}"
            ) from exc
        out: List[Dict[str, Any]] = []
        for h in hits or []:
            text = getattr(h, "text", None) or h.get("text", "") if isinstance(h, dict) else getattr(h, "text", "")
            out.append(wrap_result(text, id=getattr(h, "id", None) or (h.get("id") if isinstance(h, dict) else None)))
        return out

    def teardown(self) -> None:
        if not self._agent_id:
            return
        try:
            self._client.agents.delete(agent_id=self._agent_id)
        except Exception:
            pass
