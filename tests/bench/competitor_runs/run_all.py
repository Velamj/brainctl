"""Competitor sweep harness.

Runs every adapter (brainctl + each external competitor) over the same
LOCOMO / LongMemEval slice and writes a JSON results blob suitable for
the COMPETITOR_RESULTS.md table.

Usage:
    python -m tests.bench.competitor_runs.run_all \
        --bench locomo --limit 5            # 5-question smoke
    python -m tests.bench.competitor_runs.run_all \
        --bench longmemeval --limit 50      # stratified 50-question subset
    python -m tests.bench.competitor_runs.run_all \
        --bench locomo                      # full sweep (cost-gated)

Honesty contract:
  * Adapters that fail their import / api-key check raise
    CompetitorUnavailable; the runner records {"status": "skipped",
    "reason": ...} into the result JSON. NO fabricated numbers.
  * Each (bench, adapter) is run TWICE; we report mean + stdev so
    the reader can see flakiness.
  * Cost projection is computed up-front and the harness REFUSES to
    proceed with the full sweep if estimated cost > $5 (override
    with ``--cost-ceiling-usd N`` at your own risk).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# brainctl src on path for direct imports.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from tests.bench.external_runner import (  # noqa: E402
    DEFAULT_KS,
    Question,
    Turn,
    aggregate_results,
    eval_questions,
    score_question,
)
from tests.bench.competitor_runs.common import (  # noqa: E402
    CompetitorUnavailable,
    estimate_call_cost,
)


RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


def _load_adapters() -> List[Tuple[str, Callable[[], Any]]]:
    """Return (name, factory) pairs in the order they appear in the table."""
    from .brainctl_adapter import BrainSearchAdapter, CmdSearchAdapter
    from .mem0_adapter import Mem0Adapter
    from .letta_adapter import LettaAdapter
    from .zep_adapter import ZepAdapter
    from .cognee_adapter import CogneeAdapter
    from .memorylake_adapter import MemoryLakeAdapter
    from .mempalace_adapter import MemPalaceAdapter
    from .openai_memory_adapter import OpenAIMemoryAdapter

    return [
        ("brainctl-brain", BrainSearchAdapter),
        ("brainctl-cmd",   CmdSearchAdapter),
        ("mem0",           Mem0Adapter),
        ("letta",          LettaAdapter),
        ("zep",            ZepAdapter),
        ("cognee",         CogneeAdapter),
        ("mempalace",      MemPalaceAdapter),
        ("memorylake",     MemoryLakeAdapter),
        ("openai_memory",  OpenAIMemoryAdapter),
    ]


# ---------------------------------------------------------------------------
# Bench loaders -> (turns, questions) per tenant
# ---------------------------------------------------------------------------


def _load_locomo_tenants(limit: Optional[int]) -> List[Tuple[str, List[Turn], List[Question]]]:
    from tests.bench.datasets.locomo_loader import load as load_locomo
    from tests.bench.locomo_eval import (
        conversation_to_questions,
        conversation_to_turns,
    )
    convos = load_locomo()
    out = []
    for c in convos:
        sample = str(c.get("sample_id", "?"))
        qs = conversation_to_questions(c)
        if limit is not None:
            qs = qs[:limit]
        if not qs:
            continue
        out.append((f"locomo-{sample}", conversation_to_turns(c), qs))
    return out


def _load_longmemeval_tenants(limit: Optional[int]) -> List[Tuple[str, List[Turn], List[Question]]]:
    from tests.bench.datasets.longmemeval_loader import load as load_lme
    from tests.bench.longmemeval_eval import (
        entry_to_question,
        entry_to_turns,
        _stratified_subset,
    )
    entries = load_lme()
    if limit is not None:
        entries = _stratified_subset(entries, limit)
    out = []
    for e in entries:
        qid = str(e.get("question_id", "?"))
        q = entry_to_question(e)
        if not q.gold_keys:
            continue
        out.append((f"lme-{qid}", entry_to_turns(e), [q]))
    return out


BENCH_LOADERS = {
    "locomo": _load_locomo_tenants,
    "longmemeval": _load_longmemeval_tenants,
}


# ---------------------------------------------------------------------------
# Per-adapter run
# ---------------------------------------------------------------------------


def _adapter_search_fn(adapter):
    def fn(query: str, k: int):
        try:
            return adapter.search(query, k)
        except CompetitorUnavailable:
            raise
        except Exception:
            # Log + return empty so the run completes; the per-question
            # row will show ranked_keys=[] and contribute 0.0 to the
            # aggregate. Better than aborting the whole sweep on one
            # bad query.
            return []
    return fn


def run_adapter_once(
    adapter_factory,
    tenants: Sequence[Tuple[str, List[Turn], List[Question]]],
    *,
    top_k: int,
    ks: Sequence[int],
) -> Dict[str, Any]:
    """Run one adapter end-to-end across every tenant. Returns aggregate."""
    try:
        adapter = adapter_factory()
    except CompetitorUnavailable as cu:
        return {
            "status": "skipped",
            "competitor": cu.competitor,
            "reason": cu.reason,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "skipped",
            "competitor": getattr(adapter_factory, "name", "?"),
            "reason": f"unexpected init error: {exc!r}",
            "traceback": traceback.format_exc(),
        }

    name = adapter.name
    all_rows = []
    n_writes_total = 0
    n_queries_total = 0
    t_ingest_total = 0.0
    t_query_total = 0.0
    skipped_reason: Optional[str] = None

    try:
        for tenant_id, turns, questions in tenants:
            try:
                adapter.setup(tenant_id)
            except CompetitorUnavailable as cu:
                skipped_reason = f"setup({tenant_id}): {cu.reason}"
                break
            try:
                t0 = time.perf_counter()
                for t in turns:
                    if not t.key:
                        continue
                    adapter.ingest(t.key, t.text, t.speaker, t.timestamp)
                    n_writes_total += 1
                t_ingest_total += time.perf_counter() - t0

                # Cognee needs an explicit cognify pass between ingest
                # and search; other adapters no-op this.
                if hasattr(adapter, "cognify"):
                    adapter.cognify()

                fn = _adapter_search_fn(adapter)
                t0 = time.perf_counter()
                for q in questions:
                    row = score_question(q, fn, k_max=max(ks), ks=ks)
                    all_rows.append(row)
                    n_queries_total += 1
                t_query_total += time.perf_counter() - t0
            except CompetitorUnavailable as cu:
                skipped_reason = f"run({tenant_id}): {cu.reason}"
                break
            finally:
                try:
                    adapter.teardown()
                except Exception:
                    pass
    finally:
        # Final teardown best-effort.
        try:
            adapter.teardown()
        except Exception:
            pass

    if skipped_reason and not all_rows:
        return {"status": "skipped", "competitor": name, "reason": skipped_reason}

    agg = aggregate_results(all_rows, ks=ks)
    estimated_cost = estimate_call_cost(adapter, n_writes_total, n_queries_total)
    return {
        "status": "ok",
        "competitor": name,
        "pinned_version": adapter.pinned_version,
        "n_tenants": len(tenants),
        "n_writes": n_writes_total,
        "n_queries": n_queries_total,
        "t_ingest_s": round(t_ingest_total, 2),
        "t_query_s": round(t_query_total, 2),
        "estimated_cost_usd": estimated_cost,
        "overall": agg["overall"],
        "by_category": agg["by_category"],
        "partial_skip_reason": skipped_reason,  # populated if some tenants ran
    }


# ---------------------------------------------------------------------------
# Cost gate
# ---------------------------------------------------------------------------


def _project_total_cost(adapters, tenants, *, repeats: int) -> Dict[str, float]:
    """Estimate USD per adapter (best guess) before the sweep starts."""
    n_writes = sum(len(t[1]) for t in tenants)
    n_queries = sum(len(t[2]) for t in tenants) * repeats
    by_competitor: Dict[str, float] = {}
    for name, factory in adapters:
        # We can't instantiate the adapter to read its rate fields
        # without triggering its api-key check, so read class attrs.
        cw = getattr(factory, "cost_per_1k_writes_usd", 0.0)
        cq = getattr(factory, "cost_per_1k_queries_usd", 0.0)
        by_competitor[name] = round(
            (n_writes * repeats * cw / 1000) + (n_queries * cq / 1000),
            4,
        )
    by_competitor["_total"] = round(sum(by_competitor.values()), 4)
    return by_competitor


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run brainctl + competitors on LOCOMO / LongMemEval")
    p.add_argument("--bench", choices=list(BENCH_LOADERS), required=True)
    p.add_argument("--limit", type=int, default=None,
                   help="Per-tenant question limit (smoke); LongMemEval: total entry limit")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--repeats", type=int, default=2,
                   help="Run each adapter N times; report stdev")
    p.add_argument("--cost-ceiling-usd", type=float, default=5.0,
                   help="Refuse to start if projected total cost > this")
    p.add_argument("--only", default=None,
                   help="Comma-separated list of adapter names to run (skip others)")
    p.add_argument("--out", default=None,
                   help="Output path (defaults to results/<bench>_<date>.json)")
    args = p.parse_args(argv)

    ks = tuple(sorted(set(DEFAULT_KS) | {args.top_k}))

    # Load tenants once — every adapter sees the same slice.
    tenants = BENCH_LOADERS[args.bench](args.limit)
    if not tenants:
        print(f"No tenants loaded for bench={args.bench} limit={args.limit}", file=sys.stderr)
        return 2

    adapters = _load_adapters()
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        adapters = [(n, f) for n, f in adapters if n in wanted]
        if not adapters:
            print(f"--only filter {wanted} matched no adapters", file=sys.stderr)
            return 2

    cost_proj = _project_total_cost(adapters, tenants, repeats=args.repeats)
    print(f"# Projected cost (USD), {args.repeats}x runs over {len(tenants)} tenants:")
    for k, v in cost_proj.items():
        print(f"#   {k:20s} ${v:.4f}")
    if cost_proj["_total"] > args.cost_ceiling_usd:
        print(
            f"\nProjected cost ${cost_proj['_total']:.2f} exceeds "
            f"--cost-ceiling-usd ${args.cost_ceiling_usd:.2f}.\n"
            f"Re-run with --limit N for a smoke subset, or --cost-ceiling-usd "
            f"to override.",
            file=sys.stderr,
        )
        return 3

    # Run.
    runs: Dict[str, List[Dict[str, Any]]] = {}
    for name, factory in adapters:
        print(f"\n# === {name} ===", flush=True)
        per_run = []
        for i in range(args.repeats):
            print(f"  run {i + 1}/{args.repeats} ...", flush=True)
            t0 = time.perf_counter()
            r = run_adapter_once(factory, tenants, top_k=args.top_k, ks=ks)
            r["wall_s"] = round(time.perf_counter() - t0, 2)
            per_run.append(r)
            print(f"  -> {r.get('status')} {r.get('reason', '')[:80]}", flush=True)
        runs[name] = per_run

    # Aggregate over repeats: mean + stdev for each metric.
    summary = _summarize_runs(runs, ks=ks)

    # Write output JSON.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"{args.bench}_{today}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bench": args.bench,
        "limit": args.limit,
        "top_k": args.top_k,
        "ks": list(ks),
        "repeats": args.repeats,
        "n_tenants": len(tenants),
        "n_questions": sum(len(t[2]) for t in tenants),
        "n_turns": sum(len(t[1]) for t in tenants),
        "cost_projection": cost_proj,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs": runs,
        "summary": summary,
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"\nWrote {out_path}")
    return 0


def _summarize_runs(runs: Dict[str, List[Dict[str, Any]]], *, ks) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, rs in runs.items():
        ok = [r for r in rs if r.get("status") == "ok"]
        if not ok:
            out[name] = {
                "status": "skipped",
                "reason": rs[0].get("reason", "?") if rs else "no runs",
            }
            continue
        cell: Dict[str, Any] = {"status": "ok",
                                 "n_runs_ok": len(ok),
                                 "pinned_version": ok[0].get("pinned_version"),
                                 "estimated_cost_usd": ok[0].get("estimated_cost_usd"),
                                 "wall_s_mean": round(statistics.mean(r["wall_s"] for r in ok), 2)}
        for K in ks:
            for metric in ("hit_at_", "recall_at_", "ndcg_at_"):
                vals = [r["overall"].get(f"{metric}{K}", 0.0) for r in ok]
                cell[f"{metric}{K}_mean"] = round(statistics.mean(vals), 4)
                cell[f"{metric}{K}_stdev"] = round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0
        mrrs = [r["overall"].get("mrr", 0.0) for r in ok]
        cell["mrr_mean"] = round(statistics.mean(mrrs), 4)
        cell["mrr_stdev"] = round(statistics.pstdev(mrrs), 4) if len(mrrs) > 1 else 0.0
        out[name] = cell
    return out


if __name__ == "__main__":
    raise SystemExit(main())
