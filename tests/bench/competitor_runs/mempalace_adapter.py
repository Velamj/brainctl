"""MemPalace adapter — uses the local mempalace SDK (pip install mempalace).

Pinned to mempalace==3.3.1 (PyPI 2026-04). MemPalace docs:
https://mempalaceofficial.com/reference/python-api

MemPalace runs fully local — no API key, no LLM calls required for the
core retrieval path (per their README: "Nothing leaves your machine
unless you opt in" and "No API key... for the core benchmark path").
That keeps the cost-per-1k fields at 0.

How MemPalace stores: ``palace.add(text, ...)`` writes raw content; we
do not invoke their fact-extraction or graph layers because we want a
like-for-like comparison against brainctl's Brain.search baseline (FTS
+ optional vec). Each per-conversation tenant is namespaced via the
``mempalace init <path>`` directory (one ephemeral palace per
conversation), torn down by deleting the dir.

MemPalace's own published LOCOMO numbers (README, 2026-04):
  * "session, top-10, no rerank":   R@10 60.3% on 1,986 LOCOMO Qs
  * "hybrid v5, top-10, no rerank": R@10 88.9% on 1,986 LOCOMO Qs
  * LongMemEval R@5: 96.6% (raw semantic), 98.4% (hybrid pipeline)

The adapter intentionally exercises the basic top-K retrieve path so
the published 60.3% number is what we'd measure if the SDK matches its
README. The hybrid v5 path is opt-in and the README does not specify
which knobs flip it on; if we can find them we'll add a second adapter.

Note: a previous brainctl session claimed "we crushed mempalace" — that
claim has no measured basis. Until this adapter is run on the same
LOCOMO fixtures as brainctl, the only honest comparison is the one in
mempalace's README, which puts their basic top-10 retrieval at parity
with brainctl Brain.search (R@10 60.3% vs 60.4%) and their hybrid
substantially ahead (R@10 88.9% vs our 60.4%).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from .common import (
    CompetitorUnavailable,
    short_text_for,
    wrap_result,
)


class MemPalaceAdapter:
    name = "mempalace"
    pinned_version = "3.3.1"
    needs_api_key = False
    cost_per_1k_writes_usd = 0.0
    cost_per_1k_queries_usd = 0.0

    def __init__(self) -> None:
        try:
            import mempalace  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise CompetitorUnavailable(
                self.name,
                f"mempalace SDK not installed (pip install mempalace=={self.pinned_version}): {exc!r}",
            ) from exc
        self._palace = None
        self._palace_dir: Path | None = None

    def setup(self, tenant_id: str) -> None:
        try:
            from mempalace import Palace  # type: ignore
        except ImportError as exc:
            raise CompetitorUnavailable(
                self.name,
                f"mempalace.Palace import failed: {exc!r}",
            ) from exc
        self._palace_dir = Path(tempfile.mkdtemp(prefix=f"mempalace-{tenant_id}-"))
        try:
            self._palace = Palace(path=str(self._palace_dir))
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"Palace init failed: {exc!r}"
            ) from exc

    def ingest(
        self,
        key: str,
        text: str,
        speaker: str = "",
        timestamp: str = "",
    ) -> None:
        if self._palace is None:
            raise CompetitorUnavailable(self.name, "setup() not called")
        body = short_text_for(key, text, speaker, timestamp)
        try:
            # MemPalace's documented add signature is `palace.add(text)`;
            # if their SDK exposes `add(text, metadata=...)` we ignore the
            # metadata channel here so the [key=...] marker (already in
            # `body`) is what survives the round-trip — same honesty
            # contract as the Mem0 adapter's infer=False.
            self._palace.add(body)
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"add failed: {exc!r}"
            ) from exc

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        if self._palace is None:
            raise CompetitorUnavailable(self.name, "setup() not called")
        try:
            hits = self._palace.search(query, top_k=top_k)
        except TypeError:
            try:
                hits = self._palace.search(query, k=top_k)
            except Exception as exc:  # noqa: BLE001
                raise CompetitorUnavailable(
                    self.name, f"search failed: {exc!r}"
                ) from exc
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"search failed: {exc!r}"
            ) from exc
        out: List[Dict[str, Any]] = []
        for h in hits or []:
            if isinstance(h, str):
                text = h
                score = 0.0
                hid = None
            elif isinstance(h, dict):
                text = h.get("text") or h.get("content") or h.get("memory") or ""
                score = h.get("score", 0.0)
                hid = h.get("id")
            else:
                text = getattr(h, "text", "") or getattr(h, "content", "") or str(h)
                score = float(getattr(h, "score", 0.0) or 0.0)
                hid = getattr(h, "id", None)
            out.append(wrap_result(text, score=score, id=hid))
        return out

    def teardown(self) -> None:
        self._palace = None
        if self._palace_dir and self._palace_dir.exists():
            try:
                shutil.rmtree(self._palace_dir)
            except Exception:
                pass
        self._palace_dir = None
