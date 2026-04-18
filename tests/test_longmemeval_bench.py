"""Regression gate for the LongMemEval (oracle) retrieval-only benchmark.

Skipped by default. Enable with::

    BRAINCTL_RUN_BENCH=1           pytest tests/test_longmemeval_bench.py
    BRAINCTL_RUN_BENCH=longmemeval pytest tests/test_longmemeval_bench.py

What this gates:
  * Headline retrieval metrics on the four "retrieval-friendly" axes
    (single-session-user / -assistant / -preference, multi-session)
    stay within tolerance of ``tests/bench/baselines/longmemeval.json``.
  * Per-axis numbers stay informational — the gate is on overall only,
    same convention as ``test_search_quality_bench.py``.

Runtime is the constraining factor here: the full oracle split is
~500 entries, each spinning up a temp DB. By default we run a 50-entry
subset (~30s); set ``BRAINCTL_BENCH_FULL=1`` for the entire split.
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
BASELINE_PATH = _REPO_ROOT / "tests" / "bench" / "baselines" / "longmemeval.json"
SUBSET_BASELINE_PATH = (
    _REPO_ROOT / "tests" / "bench" / "baselines" / "longmemeval_subset50.json"
)

# CI uses the wider 5% tolerance on the 50-entry stratified subset because
# small per-axis cells (~12 entries each) swing more than the 2% gate would
# allow. The full-sweep baseline at BASELINE_PATH stays at the tighter 2%.
TOLERANCE = 0.05
GATED_METRICS = ("hit_at_1", "hit_at_5", "mrr", "ndcg_at_5", "recall_at_5")
DEFAULT_LIMIT = 50         # subset for CI; ~6s runtime


def _bench_enabled() -> bool:
    if not BENCH_GATE:
        return False
    return BENCH_GATE in ("1", "all", "longmemeval", "true", "yes")


pytestmark = pytest.mark.skipif(
    not _bench_enabled(),
    reason=("LongMemEval bench is opt-in. Set BRAINCTL_RUN_BENCH=1 (or "
            "=longmemeval) to run."),
)


@pytest.fixture(scope="module", autouse=True)
def _seed_rng():
    random.seed(42)


@pytest.fixture(scope="module")
def baseline():
    """Pick the right baseline for the run mode.

    The 50-entry stratified subset has smaller per-axis cells than the
    full sweep, so its overall numbers drift slightly from the full
    baseline. We commit a separate ``longmemeval_subset50.json`` that
    matches the CI default and use the full one only when
    ``BRAINCTL_BENCH_FULL=1``.
    """
    path = BASELINE_PATH if BENCH_FULL else SUBSET_BASELINE_PATH
    if not path.exists():
        which = "longmemeval" if BENCH_FULL else "longmemeval (subset)"
        regen = (
            "python3 -m tests.bench.run --bench longmemeval --update-baseline"
            if BENCH_FULL
            else "python3 -m tests.bench.run --bench longmemeval --limit 50 "
                 "--update-baseline --baseline-name longmemeval_subset50"
        )
        pytest.fail(f"Missing {which} baseline at {path}. Regen with:\n    {regen}")
    with path.open() as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def bench_result():
    from tests.bench.longmemeval_eval import run as run_lme
    limit = None if BENCH_FULL else DEFAULT_LIMIT
    backend = os.environ.get("BRAINCTL_BENCH_BACKEND", "brain")
    return run_lme(backend=backend, limit=limit)


def test_baseline_has_gated_metrics(baseline):
    assert "overall" in baseline
    for metric in GATED_METRICS:
        assert metric in baseline["overall"], (
            f"Baseline missing gated metric {metric!r}; refresh with "
            "`--update-baseline`."
        )


def test_no_regression(bench_result, baseline):
    cur_o = bench_result["overall"]
    base_o = baseline["overall"]
    failing = []
    for metric in GATED_METRICS:
        cur = float(cur_o.get(metric, 0.0))
        base = float(base_o.get(metric, 0.0))
        if cur < base - TOLERANCE:
            failing.append((metric, cur, base))
    if failing:
        lines = [f"LongMemEval bench regressed beyond {TOLERANCE * 100:.0f}%:"]
        for m, c, b in failing:
            lines.append(f"  {m}: current={c:.4f} baseline={b:.4f}")
        lines.append(
            "Refresh with: python3 -m tests.bench.run --bench longmemeval "
            "--update-baseline"
        )
        pytest.fail("\n".join(lines))


def test_no_silent_improvement(bench_result, baseline):
    cur_o = bench_result["overall"]
    base_o = baseline["overall"]
    for metric in GATED_METRICS:
        delta = float(cur_o.get(metric, 0.0)) - float(base_o.get(metric, 0.0))
        assert delta <= 0.15, (
            f"{metric} improved by {delta:.4f} vs baseline — refresh "
            "the baseline or investigate what changed."
        )
