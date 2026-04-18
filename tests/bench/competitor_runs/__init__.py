"""Competitor benchmark runners.

This package wires Mem0 / Letta / Zep / Cognee / MemoryLake / OpenAI Memory
into the same ``SearchFn`` shape as ``tests/bench/external_runner.py`` so
they can be scored side-by-side with brainctl on LOCOMO + LongMemEval.

Reproducibility / honesty rules (see COMPETITOR_RESULTS.md "Methodology"):
  * each adapter MUST raise ``CompetitorUnavailable`` (not a silent skip
    or fake result) when its SDK / API key / model isn't reachable.
  * each adapter writes one record per ingested turn — no batched
    dedup, no LLM-derived "summarised memory" stage that competitors
    bolt on by default — to keep the gold-evidence matching identical
    across systems.
  * ``setup.sh`` documents version pins for every SDK so a re-runner
    gets the same numbers months later.
"""

from .common import (  # noqa: F401
    CompetitorAdapter,
    CompetitorUnavailable,
    SkippedResult,
    estimate_call_cost,
    short_text_for,
)

__all__ = [
    "CompetitorAdapter",
    "CompetitorUnavailable",
    "SkippedResult",
    "estimate_call_cost",
    "short_text_for",
]
