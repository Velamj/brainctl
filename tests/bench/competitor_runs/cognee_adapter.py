"""Cognee adapter — local-first by default; uses OpenAI embeddings.

Pinned to cognee==1.0.0. Cognee docs: https://docs.cognee.ai/

Cognee builds a *knowledge graph* over ingested text using an LLM
extractor (``cognee.cognify``) — that's its core differentiator vs
flat-vector competitors. For honest scoring we have two options:

  1. Run cognify (default) — graph nodes/edges replace raw text in
     the searchable substrate. The [key=...] marker is preserved in
     node properties because the chunker keeps the original chunk
     text on each node. This is the "recommended config" per Cognee
     docs and what we run.
  2. Skip cognify and rely on the raw vector store — cheaper, but
     not what Cognee is *for*. Documented in the report as a caveat.

We use option 1 with the Cognee SQLite vector adapter (no Postgres /
Neo4j required for local dev). Cost: cognify is LLM-heavy. Estimated
$3-8 for a full LOCOMO sweep depending on chunk count. Within budget
if we use ``cost_ceiling_usd=5`` in the runner.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from .common import (
    CompetitorUnavailable,
    require_env,
    short_text_for,
    wrap_result,
)


class CogneeAdapter:
    name = "cognee"
    pinned_version = "1.0.0"
    needs_api_key = True   # OPENAI_API_KEY for the extractor
    cost_per_1k_writes_usd = 0.50    # rough — cognify is LLM-heavy
    cost_per_1k_queries_usd = 0.02   # embedding-only at query time

    def __init__(self) -> None:
        try:
            import cognee  # type: ignore
        except ImportError as exc:
            raise CompetitorUnavailable(
                self.name,
                f"cognee not installed (pip install cognee=={self.pinned_version}): {exc!r}",
            ) from exc
        require_env("OPENAI_API_KEY", self.name)
        self._cognee = cognee
        self._dataset: str = ""
        self._loop = asyncio.new_event_loop()

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def setup(self, tenant_id: str) -> None:
        # Cognee's "dataset" is its tenant boundary. Each LOCOMO convo
        # gets its own so cross-conversation contamination is impossible.
        self._dataset = f"brainctl-bench-{tenant_id}"
        try:
            # Reset the dataset to ensure a clean baseline.
            self._run(self._cognee.prune.prune_data())
            self._run(self._cognee.prune.prune_system(metadata=True))
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"prune failed: {exc!r}"
            ) from exc

    def ingest(
        self,
        key: str,
        text: str,
        speaker: str = "",
        timestamp: str = "",
    ) -> None:
        body = short_text_for(key, text, speaker, timestamp)
        try:
            self._run(self._cognee.add(body, dataset_name=self._dataset))
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"add failed: {exc!r}"
            ) from exc

    def cognify(self) -> None:
        """Run the LLM extraction pass. Called once per tenant after all
        ingest()s, NOT per turn — cognify is expensive and batches well."""
        try:
            self._run(self._cognee.cognify([self._dataset]))
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"cognify failed: {exc!r}"
            ) from exc

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        try:
            from cognee.api.v1.search import SearchType  # type: ignore
            hits = self._run(
                self._cognee.search(
                    query_type=SearchType.CHUNKS,
                    query_text=query,
                    datasets=[self._dataset],
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"search failed: {exc!r}"
            ) from exc
        out: List[Dict[str, Any]] = []
        for h in (hits or [])[:top_k]:
            text = h.get("text") if isinstance(h, dict) else str(h)
            out.append(wrap_result(text or ""))
        return out

    def teardown(self) -> None:
        try:
            self._run(self._cognee.prune.prune_data())
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass
