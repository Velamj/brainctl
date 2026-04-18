"""Regression gate for the LOCOMO retrieval-only benchmark.

Skipped by default (slow + needs the dataset). Enable with
``BRAINCTL_RUN_BENCH=1`` (or ``=locomo`` for just this one):

    BRAINCTL_RUN_BENCH=1 pytest tests/test_locomo_bench.py
    BRAINCTL_RUN_BENCH=locomo pytest tests/test_locomo_bench.py

What this gates:
  * The ``Brain.search`` (FTS5-only) backend stays within tolerance of
    ``tests/bench/baselines/locomo.json`` on a single conversation. We
    use a single convo by default for CI runtime sanity (~13s instead
    of the ~270s full sweep). Set ``BRAINCTL_BENCH_FULL=1`` to gate the
    whole 10-conversation sweep instead.

Why ``Brain.search`` and not ``cmd_search``:
  * The committed baseline is captured against the production
    ``cmd_search`` pipeline for the headline number. Per-convo on the
    Brain.search path is what stays cheap enough to run under tolerance
    every time. After Worker A's reranker fix lands, the orchestrator
    will rerun ``--backend cmd`` against the merged main and diff against
    ``tests/bench/baselines/locomo_pre_fix_2026_04_18.json`` (the
    historical pre-fix capture).
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

BENCH_GATE = os.environ.get("BRAINCTL_RUN_BENCH", "")
BENCH_FULL = os.environ.get("BRAINCTL_BENCH_FULL", "") == "1"

_REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = _REPO_ROOT / "tests" / "bench" / "baselines" / "locomo.json"

# Match the search-quality bench tolerance — small enough to catch real
# regressions, big enough to absorb day-to-day RNG jitter.
TOLERANCE = 0.02

# Headline metrics gated. Aligns with LOCOMO_GATED in tests/bench/run.py.
GATED_METRICS = ("hit_at_1", "hit_at_5", "mrr", "ndcg_at_5", "recall_at_5")


def _bench_enabled() -> bool:
    """Run when the env var is unset to "" → off, or "1"/"locomo"/"all"."""
    if not BENCH_GATE:
        return False
    return BENCH_GATE in ("1", "all", "locomo", "true", "yes")


pytestmark = pytest.mark.skipif(
    not _bench_enabled(),
    reason=("LOCOMO bench is opt-in. Set BRAINCTL_RUN_BENCH=1 (or "
            "=locomo) to run."),
)


@pytest.fixture(scope="module", autouse=True)
def _seed_rng():
    """Same seeding as the CLI runner so the bench is deterministic."""
    random.seed(42)


@pytest.fixture(scope="module")
def baseline():
    if not BASELINE_PATH.exists():
        pytest.fail(
            f"Missing baseline at {BASELINE_PATH}. Generate it with:\n"
            f"    python3 -m tests.bench.run --bench locomo --backend cmd "
            f"--update-baseline"
        )
    with BASELINE_PATH.open() as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def bench_result():
    from tests.bench.locomo_eval import run as run_locomo
    convo_idx = None if BENCH_FULL else 0
    # Use the brain backend by default — fast and stable. Set
    # BRAINCTL_BENCH_BACKEND=cmd to gate the hybrid pipeline instead.
    backend = os.environ.get("BRAINCTL_BENCH_BACKEND", "brain")
    return run_locomo(backend=backend, convo_idx=convo_idx)


def test_baseline_has_gated_metrics(baseline):
    """The baseline JSON must carry every metric we gate on."""
    assert "overall" in baseline
    for metric in GATED_METRICS:
        assert metric in baseline["overall"], (
            f"Baseline missing gated metric {metric!r}; was the JSON written "
            "by an older version of tests/bench/run.py?"
        )


def test_no_regression(bench_result, baseline):
    """No headline metric may drop more than TOLERANCE below baseline."""
    cur_o = bench_result["overall"]
    base_o = baseline["overall"]
    failing = []
    for metric in GATED_METRICS:
        cur = float(cur_o.get(metric, 0.0))
        base = float(base_o.get(metric, 0.0))
        if cur < base - TOLERANCE:
            failing.append((metric, cur, base))
    if failing:
        msg_lines = [
            f"LOCOMO bench regressed beyond tolerance ({TOLERANCE * 100:.0f}%):"
        ]
        for m, c, b in failing:
            msg_lines.append(f"  {m}: current={c:.4f} baseline={b:.4f}")
        msg_lines.append(
            "Either fix the regression or refresh the baseline with:\n"
            "    python3 -m tests.bench.run --bench locomo --backend cmd "
            "--update-baseline"
        )
        pytest.fail("\n".join(msg_lines))


def test_no_silent_improvement(bench_result, baseline):
    """A >15% improvement usually means the fixture / metric semantics
    shifted — flag it instead of letting the baseline drift unrecorded."""
    cur_o = bench_result["overall"]
    base_o = baseline["overall"]
    for metric in GATED_METRICS:
        delta = float(cur_o.get(metric, 0.0)) - float(base_o.get(metric, 0.0))
        assert delta <= 0.15, (
            f"{metric} improved by {delta:.4f} vs baseline "
            f"{base_o.get(metric, 0.0):.4f} — refresh the baseline or "
            f"investigate what changed."
        )
