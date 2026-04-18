"""Shared types for competitor adapters.

The ``CompetitorAdapter`` protocol mirrors ``SearchFn`` from
``tests/bench/external_runner.py`` exactly:

    search_fn(query: str, k: int) -> List[Dict[str, Any]]

Plus a small surface for ingest + cost accounting + lifecycle (so the
run harness can spin per-conversation tenants up cleanly and tear them
down — Mem0/Zep are remote-hosted, leaving stale state would pollute
later runs and inflate costs).

The honesty contract: when a competitor's SDK isn't installed, when
its API key is missing, or when its endpoint refuses the request,
the adapter raises ``CompetitorUnavailable`` — it does NOT return
empty results. The runner records the skip in the result JSON so the
report can show "skipped — reason X" instead of a fabricated 0.0.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol


# ---------------------------------------------------------------------------
# Sentinels + exceptions
# ---------------------------------------------------------------------------


class CompetitorUnavailable(Exception):
    """Raised when a competitor adapter cannot run.

    Distinct from a 0.0 score: a 0.0 is a measured miss, an
    Unavailable is "we did not measure". The runner treats them
    differently in the result JSON.
    """

    def __init__(self, competitor: str, reason: str):
        self.competitor = competitor
        self.reason = reason
        super().__init__(f"{competitor}: {reason}")


@dataclass
class SkippedResult:
    """Recorded into the results JSON when an adapter raises Unavailable."""

    competitor: str
    reason: str
    estimated_cost_usd: Optional[float] = None
    notes: str = ""


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


class CompetitorAdapter(Protocol):
    """The minimum surface every competitor adapter must implement.

    Lifecycle is per-conversation (LOCOMO) or per-entry (LongMemEval):
        a = AdapterCls()
        a.setup(tenant_id="locomo-3")        # may create remote namespace
        for turn in turns:
            a.ingest(turn.key, turn.text, turn.speaker, turn.timestamp)
        for q in questions:
            results = a.search(q.question, top_k=20)
        a.teardown()                          # may delete remote namespace
    """

    name: str
    pinned_version: str
    needs_api_key: bool
    cost_per_1k_writes_usd: float
    cost_per_1k_queries_usd: float

    def setup(self, tenant_id: str) -> None: ...

    def ingest(
        self,
        key: str,
        text: str,
        speaker: str = "",
        timestamp: str = "",
    ) -> None: ...

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]: ...

    def teardown(self) -> None: ...


# ---------------------------------------------------------------------------
# Helpers shared by every adapter
# ---------------------------------------------------------------------------


# The LOCOMO/LongMemEval gold-evidence matcher (key_for_result in
# external_runner.py) finds "[key=...]" in the returned text. Every
# adapter must persist that marker verbatim so the matcher works.
KEY_MARKER_TEMPLATE = "[key={key}]"


def short_text_for(key: str, text: str, speaker: str, timestamp: str) -> str:
    """Render a turn into the canonical ingest string used by every adapter.

    Mirrors ``external_runner.format_turn`` so a competitor's stored
    text is byte-for-byte identical to brainctl's. This is critical:
    the gold-evidence matcher (``KEY_RE`` in external_runner.py)
    re-extracts ``[key=<id>]`` from the returned content. If a
    competitor's pipeline strips that marker (some Mem0 / Cognee
    "fact extraction" passes do), the adapter MUST disable that
    pre-processing — otherwise scoring is artificially zero.
    """
    prefix = f"[{speaker}"
    if timestamp:
        prefix += f" @ {timestamp}"
    prefix += "]"
    return f"{prefix} {text} {KEY_MARKER_TEMPLATE.format(key=key)}"


def estimate_call_cost(
    adapter: CompetitorAdapter,
    n_writes: int,
    n_queries: int,
) -> float:
    """Project total USD cost for a run. Uses the adapter's published rates."""
    return round(
        (n_writes * adapter.cost_per_1k_writes_usd / 1000)
        + (n_queries * adapter.cost_per_1k_queries_usd / 1000),
        4,
    )


def require_env(name: str, competitor: str) -> str:
    """Fetch an env var or raise ``CompetitorUnavailable`` cleanly."""
    val = os.environ.get(name)
    if not val:
        raise CompetitorUnavailable(competitor, f"missing env var {name}")
    return val


# ---------------------------------------------------------------------------
# Result wrapping — every adapter must return dicts in this shape
# ---------------------------------------------------------------------------


def wrap_result(text: str, score: float = 0.0, **extra: Any) -> Dict[str, Any]:
    """Return a result dict in the shape ``key_for_result`` expects.

    The matcher only reads the ``content`` / ``summary`` / ``name``
    field. Extra fields (``score``, ``id``, ...) are passed through for
    debugging but not scored.
    """
    return {"content": text, "score": float(score), **extra}
