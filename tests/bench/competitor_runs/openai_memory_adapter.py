"""OpenAI Memory baseline — the lazy-default users start from.

There is no public ``openai.memory`` SDK surface as of the OpenAI
Python SDK 2.32.0 (April 2026). "OpenAI Memory" in the consumer
ChatGPT product is a closed, server-side feature that ChatGPT uses to
personalise its own responses — there is no developer API to read /
write it programmatically, no isolated tenant per user, no top-k
retrieval call we can score.

To still give a fair "OpenAI baseline" in the comparison, we use the
**Assistants API + File Search** stack, which is the closest officially
supported developer-facing analogue:

  setup:    client.beta.vector_stores.create(name=tenant_id)
  ingest:   client.beta.vector_stores.files.upload_and_poll(...)
            (one file per turn — File Search splits + embeds)
  search:   the vector_store has no public top-k search endpoint;
            instead, you run a Thread + Assistant turn referencing
            it, and the model decides which chunks to retrieve.

Because the second step requires a chat completion to surface
retrieved chunks, this baseline is structurally noisier than the
others (the LLM is in the loop, may reorder / drop chunks, etc.).
We document this caveat in the report instead of pretending it's
apples-to-apples.

If you want a true "raw OpenAI vector_store retrieval" baseline,
``client.beta.vector_stores.search`` is in beta as of April 2026 —
we use that path here when the SDK exposes it; otherwise we mark
the run unavailable. This adapter prefers the beta search method.

Cost (April 2026 OpenAI rates):
  * file storage:           $0.10/GB/day  (negligible for bench)
  * embedding (ingest):     $0.10 per 1M tokens (text-embedding-3-small)
  * search (vector_stores):  free
  * if we fall back to the chat-completion path: gpt-4o-mini ~$0.30/1M
    input tokens, which DOES burn money. Default disabled.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .common import (
    CompetitorUnavailable,
    require_env,
    short_text_for,
    wrap_result,
)


class OpenAIMemoryAdapter:
    name = "openai_memory"
    pinned_version = "2.32.0"
    needs_api_key = True
    # Embedding-only cost; we use the search() path, not the
    # chat-completion fallback.
    cost_per_1k_writes_usd = 0.005     # ~50 emb tokens per turn
    cost_per_1k_queries_usd = 0.001    # search() itself is free

    def __init__(self) -> None:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise CompetitorUnavailable(
                self.name,
                f"openai SDK not installed (pip install openai>={self.pinned_version}): {exc!r}",
            ) from exc
        require_env("OPENAI_API_KEY", self.name)
        try:
            self._client = OpenAI()
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"OpenAI init failed: {exc!r}"
            ) from exc

        # Verify the beta.vector_stores surface exists; if not, this
        # baseline is not runnable on this SDK version.
        if not hasattr(self._client.beta, "vector_stores"):
            raise CompetitorUnavailable(
                self.name,
                "openai.beta.vector_stores not present on this SDK version; "
                "upgrade to >=2.32.0",
            )
        self._vector_store_id: str = ""

    def setup(self, tenant_id: str) -> None:
        try:
            vs = self._client.beta.vector_stores.create(
                name=f"brainctl-bench-{tenant_id}",
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"vector_stores.create failed: {exc!r}"
            ) from exc
        self._vector_store_id = vs.id

    def ingest(
        self,
        key: str,
        text: str,
        speaker: str = "",
        timestamp: str = "",
    ) -> None:
        body = short_text_for(key, text, speaker, timestamp)
        # File Search wants files, not raw strings — wrap the turn in
        # a temporary in-memory file. One file per turn keeps gold
        # boundaries crisp (chunker won't cross [key=...] markers).
        import io
        try:
            self._client.beta.vector_stores.files.upload_and_poll(
                vector_store_id=self._vector_store_id,
                file=("turn.txt", io.BytesIO(body.encode("utf-8"))),
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"file upload failed: {exc!r}"
            ) from exc

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        try:
            res = self._client.beta.vector_stores.search(
                vector_store_id=self._vector_store_id,
                query=query,
                max_num_results=top_k,
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"vector_stores.search failed: {exc!r}"
            ) from exc
        out: List[Dict[str, Any]] = []
        for r in getattr(res, "data", []) or []:
            chunks = getattr(r, "content", []) or []
            text = "\n".join(getattr(c, "text", "") for c in chunks)
            out.append(wrap_result(text, score=getattr(r, "score", 0.0)))
        return out

    def teardown(self) -> None:
        if not self._vector_store_id:
            return
        try:
            self._client.beta.vector_stores.delete(self._vector_store_id)
        except Exception:
            pass
