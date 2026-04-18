"""Latency regression gate.

Compares a fresh harness run against the committed
``tests/bench/baselines/latency.json`` and fails if any p95 metric
regressed by more than ``REGRESSION_THRESHOLD`` (25% by default).

Why 25%
-------
On a laptop, day-to-day p95 jitter from background processes (Spotlight,
Time Machine, browser tab waking) is real. Tighter thresholds (10-15%)
flap. Looser thresholds (>30%) miss the kind of regression where someone
adds an unbatched commit inside a hot loop. 25% is the smallest threshold
that's stayed flake-free across the bench suite's existing thresholds
(``tests/bench/run.py`` uses 2% for retrieval QUALITY metrics, which are
much less noisy than wall-clock).

How to run
----------
Off by default — set ``BRAINCTL_RUN_BENCH=1`` (matches LOCOMO + LongMemEval):

    BRAINCTL_RUN_BENCH=1 pytest tests/test_latency_regression.py -v

Update the baseline after a deliberate change:

    python -m tests.bench.latency --update-baseline
    git add tests/bench/baselines/latency.json
    # commit with the intended-change explanation in the message

Why not run on every PR
-----------------------
~3-5 min wall on N=10k. Same gating model as the LOCOMO + LongMemEval
benches: opt-in for perf-relevant PRs and pre-release runs.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure we import the worktree's agentmemory, not the installed venv copy.
# (Same pattern as tests/conftest.py uses for the wider suite.)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


BENCH_GATE = os.environ.get("BRAINCTL_RUN_BENCH", "")
BASELINE_PATH = _REPO_ROOT / "tests" / "bench" / "baselines" / "latency.json"

# Mirror the constant in tests/bench/latency.py — kept duplicated rather than
# imported so this gate file can be read in isolation by humans grepping for
# "regression threshold".
REGRESSION_THRESHOLD = 1.25


def _bench_enabled() -> bool:
    if not BENCH_GATE:
        return False
    return BENCH_GATE in ("1", "all", "latency", "true", "yes")


pytestmark = pytest.mark.skipif(
    not _bench_enabled(),
    reason=("Latency bench is opt-in. Set BRAINCTL_RUN_BENCH=1 (or "
            "=latency) to run."),
)


@pytest.fixture(scope="module")
def baseline():
    if not BASELINE_PATH.exists():
        pytest.fail(
            f"Missing baseline at {BASELINE_PATH}. Generate it with:\n"
            f"    python -m tests.bench.latency --update-baseline"
        )
    with BASELINE_PATH.open() as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def fresh_run(baseline):
    """Run the harness now, against the same scales/runs as the baseline.

    We use module scope so all parametrised tests share the same fresh
    measurement — running it once instead of N-times keeps the gate fast.
    """
    from tests.bench.latency import run_sweep, OPS_DEFAULT

    scales = tuple(baseline["scales"])
    n_runs = int(baseline.get("runs_per_op", 100))
    n_warmup = int(baseline.get("warmup_per_op", 5))
    report = run_sweep(scales=scales, n_runs=n_runs, n_warmup=n_warmup,
                       ops=OPS_DEFAULT)
    return report.as_dict()


def _index(report: dict) -> dict:
    """Index results by (op, scale) for fast lookup."""
    return {(r["op"], r["scale"]): r for r in report["results"]}


def test_baseline_present(baseline):
    """Sanity: baseline file is loadable and has the schema we expect."""
    assert "results" in baseline
    assert "scales" in baseline
    assert baseline["results"], "baseline results array is empty"


def test_no_p95_regression_beyond_threshold(baseline, fresh_run):
    """Per-(op, scale): fresh p95 must be within REGRESSION_THRESHOLD of baseline.

    Reports ALL regressions in a single failure rather than failing on the
    first — gives the developer the full picture instead of one rabbit hole
    at a time.
    """
    base_idx = _index(baseline)
    fresh_idx = _index(fresh_run)

    failures: list[str] = []
    for key, base_row in base_idx.items():
        fresh_row = fresh_idx.get(key)
        if fresh_row is None:
            failures.append(f"{key[0]}@{key[1]}: missing in fresh run")
            continue
        base_p95 = base_row["p95_ms"]
        fresh_p95 = fresh_row["p95_ms"]
        # Avoid divide-by-zero on sub-millisecond ops; treat anything under
        # 0.1ms baseline as a flat regression budget instead of a ratio.
        if base_p95 < 0.1:
            limit = base_p95 + 0.5
            if fresh_p95 > limit:
                failures.append(
                    f"{key[0]}@{key[1]}: baseline {base_p95:.3f}ms p95, "
                    f"fresh {fresh_p95:.3f}ms p95 (sub-ms; budget +0.5ms)"
                )
        else:
            ratio = fresh_p95 / base_p95
            if ratio > REGRESSION_THRESHOLD:
                pct = (ratio - 1.0) * 100.0
                failures.append(
                    f"{key[0]}@{key[1]}: baseline {base_p95:.2f}ms p95, "
                    f"fresh {fresh_p95:.2f}ms p95 (+{pct:.0f}%, "
                    f"threshold +{(REGRESSION_THRESHOLD - 1.0) * 100:.0f}%)"
                )

    if failures:
        msg = (f"\n{len(failures)} latency regression(s) vs baseline:\n  "
               + "\n  ".join(failures)
               + "\n\nIf the regression is intentional, update the baseline:\n"
               + "  python -m tests.bench.latency --update-baseline\n"
               + "  git add tests/bench/baselines/latency.json")
        pytest.fail(msg)


def test_targets_met_at_target_scale(baseline):
    """Sanity: the committed baseline still meets its own targets at TARGET_SCALE.

    This is a guard against checking in a baseline where ``met_target=False``
    for any op — that would mean we accepted a known-failing baseline as the
    new normal, which is exactly what the gate is supposed to prevent.

    Operates on the COMMITTED baseline (not the fresh run) — it asks "is the
    baseline self-consistent?" rather than "did we regress?". The regression
    check above handles the comparison.
    """
    from tests.bench.latency import TARGET_SCALE
    misses = []
    for r in baseline["results"]:
        if r["scale"] != TARGET_SCALE:
            continue
        if r.get("met_target") is False:
            misses.append(
                f"{r['op']}: {r['p95_ms']:.2f}ms p95 vs target "
                f"{r['target_p95_ms']:.1f}ms"
            )
    if misses:
        pytest.fail(
            "Committed baseline contains target misses at the target scale:\n  "
            + "\n  ".join(misses)
            + "\n\nEither (a) ship the optimization that closes the gap, or "
            "(b) intentionally retune the target in tests/bench/latency.py "
            "with a comment explaining why."
        )
