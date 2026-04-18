"""Cross-encoder rerank benchmark harness.

Re-runs LOCOMO and (optionally) LongMemEval with the cross-encoder
reranker stage ON vs OFF, for each supported model. Emits a comparison
table:

    model                                    | bench    | hit@1 | hit@5 | mrr  | ndcg@5 | latency_ms
    ------------------------------------------+----------+-------+-------+------+--------+-----------
    OFF (RRF + heuristic rerankers only)     | locomo   | ...   | ...   | ...  | ...    | ...
    bge-reranker-v2-m3                        | locomo   | ...   | ...   | ...  | ...    | ...
    jina-reranker-v2-base-multilingual        | locomo   | ...   | ...   | ...  | ...    | ...
    qwen3-reranker-4b (LLM-style, deferred)   | locomo   | (skipped — not supported in 2.4.0)

Usage:
    python -m tests.bench.run_rerank_bench --bench locomo --convos 2
    python -m tests.bench.run_rerank_bench --bench longmemeval --questions 50
    python -m tests.bench.run_rerank_bench --bench both --convos 2 --questions 50

When sentence-transformers is not installed, the harness still runs
the OFF baseline and emits a row noting the rerank rows are
unavailable. The infrastructure is the deliverable; the numbers are
optional.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Make `tests` and `src` importable when invoked as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for p in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ---------------------------------------------------------------------------
# A cmd_search closure that DOES wire through args.rerank
# ---------------------------------------------------------------------------
# The existing ``tests.bench.locomo_eval._build_cmd_search_fn`` builds
# an args namespace with rerank=None implicitly. We need a variant
# that takes a rerank parameter and threads it through.

def _build_cmd_search_fn_with_rerank(db_path: Path, rerank_model: Optional[str]):
    """Mirror ``tests.bench.locomo_eval._build_cmd_search_fn`` but
    expose the cross-encoder rerank knob.

    Returns a search_fn(query, k) -> list[dict]. search_fn.errors
    accumulates exception class -> count for diagnostics.
    """
    import contextlib
    import gc
    import io
    import types
    import agentmemory._impl as _impl
    _impl.DB_PATH = db_path

    def search_fn(query: str, k: int):
        captured: list = []

        def _capture(data, compact=False):
            captured.append(data)

        args = types.SimpleNamespace(
            query=query, limit=k,
            tables="memories,events,context",
            no_recency=True, no_graph=True,
            budget=None, min_salience=None,
            mmr=False, mmr_lambda=0.7, explore=False,
            profile=None, pagerank_boost=0.0,
            quantum=False, benchmark=False,
            agent="bench-agent", format="json",
            oneline=False, verbose=False,
            # The new knob:
            rerank=rerank_model,
        )
        saved_json = _impl.json_out
        saved_oneline = _impl.oneline_out
        _impl.json_out = _capture
        _impl.oneline_out = _capture
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    _impl.cmd_search(args)
                except Exception as exc:
                    name = type(exc).__name__
                    search_fn.errors[name] = search_fn.errors.get(name, 0) + 1
                    return []
        finally:
            _impl.json_out = saved_json
            _impl.oneline_out = saved_oneline
            gc.collect()

        if not captured:
            return []
        payload = captured[0] if isinstance(captured[0], dict) else {}
        flat: List[Dict[str, Any]] = []
        for bucket in ("memories", "events", "context", "entities", "decisions"):
            flat.extend(payload.get(bucket, []) or [])
        flat.sort(key=lambda r: r.get("final_score", 0.0), reverse=True)
        return flat[:k]

    search_fn.errors = {}  # type: ignore[attr-defined]
    return search_fn


# ---------------------------------------------------------------------------
# Per-config evaluator
# ---------------------------------------------------------------------------

def _eval_locomo(rerank_model: Optional[str], n_convos: int) -> Dict[str, Any]:
    """Run LOCOMO with the given rerank model setting.

    Returns aggregated metrics + total wall-clock time + per-query
    average latency. Latency is approximate (wall time / queries) —
    good enough for the comparison table; not a precision benchmark.
    """
    from tests.bench.datasets.locomo_loader import load as load_locomo
    from tests.bench.locomo_eval import (
        _build_brain_for, conversation_to_questions, conversation_to_turns,
        BASELINE_KS,
    )
    from tests.bench.external_runner import (
        ingest_conversation_into_brain, score_question,
        aggregate_results,
    )

    convos = load_locomo()[:n_convos]
    all_rows: List[Dict[str, Any]] = []
    total_query_time = 0.0
    total_questions = 0

    for convo in convos:
        questions = conversation_to_questions(convo)
        if not questions:
            continue
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "locomo_rerank_bench.db"
            brain = _build_brain_for(db_path, agent_id=f"locomo-{convo.get('sample_id')}")
            ingest_conversation_into_brain(brain, conversation_to_turns(convo))

            # Hand the DB off to cmd_search.
            try:
                conn = brain._get_conn()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                brain.close()
            except Exception:
                pass

            search_fn = _build_cmd_search_fn_with_rerank(db_path, rerank_model)

            t0 = time.perf_counter()
            for q in questions:
                row = score_question(q, search_fn, k_max=max(BASELINE_KS), ks=BASELINE_KS)
                all_rows.append(row)
            total_query_time += time.perf_counter() - t0
            total_questions += len(questions)

    agg = aggregate_results(all_rows, ks=(1, 5, 10, 20))
    overall = agg.get("overall", agg)
    return {
        "n_questions": total_questions,
        "hit_at_1": overall.get("hit_at_1"),
        "hit_at_5": overall.get("hit_at_5"),
        "mrr": overall.get("mrr"),
        "ndcg_at_5": overall.get("ndcg_at_5"),
        "total_query_s": round(total_query_time, 2),
        "avg_latency_ms": round(1000 * total_query_time / max(total_questions, 1), 1),
    }


def _eval_longmemeval(rerank_model: Optional[str], n_questions: int) -> Dict[str, Any]:
    """Run LongMemEval (subset) with the given rerank model setting.

    LongMemEval uses much larger haystacks per question — we limit
    to n_questions to keep wall time reasonable.
    """
    try:
        from tests.bench.datasets.longmemeval_loader import load as load_lme
    except Exception as exc:  # noqa: BLE001
        return {"error": f"longmemeval loader unavailable: {exc}"}

    try:
        entries = load_lme()
    except Exception as exc:  # noqa: BLE001
        # Dataset may not be cached locally — degrade gracefully.
        return {"error": f"longmemeval dataset not available: {exc}"}

    entries = entries[:n_questions]
    if not entries:
        return {"error": "no longmemeval entries to evaluate"}

    # The longmemeval_eval module already knows how to score one entry.
    try:
        from tests.bench.longmemeval_eval import (
            _build_brain_for as _b, ingest_entry, _build_cmd_search_fn,
            score_entry,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"longmemeval_eval not importable: {exc}"}

    # Most existing harnesses don't expose a clean rerank knob in their
    # search closure, so we wrap our own. Fall back to "skipped" if
    # the entry-level scoring fn isn't there.
    return {
        "n_questions": len(entries),
        "note": "LongMemEval rerank-bench scaffolded but not numerically evaluated in this run "
                "(longmemeval_eval doesn't expose a rerank-aware search closure yet). "
                "Use --bench locomo for a numerical comparison.",
    }


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _format_table(rows: List[Dict[str, Any]]) -> str:
    """Render the comparison table as plain text."""
    cols = [
        ("config",          24),
        ("bench",           14),
        ("n_questions",      8),
        ("hit_at_1",         8),
        ("hit_at_5",         8),
        ("mrr",              7),
        ("ndcg_at_5",        9),
        ("avg_latency_ms",  14),
    ]
    header = " | ".join(name.ljust(w) for name, w in cols)
    sep = "-+-".join("-" * w for _, w in cols)
    lines = [header, sep]
    for r in rows:
        cells = []
        for name, w in cols:
            v = r.get(name, "")
            if v is None:
                v = "-"
            elif isinstance(v, float):
                v = f"{v:.4f}"
            cells.append(str(v).ljust(w))
        lines.append(" | ".join(cells))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--bench", choices=["locomo", "longmemeval", "both"], default="locomo")
    p.add_argument("--convos", type=int, default=2,
                   help="LOCOMO conversations to evaluate (default: 2; max 10)")
    p.add_argument("--questions", type=int, default=50,
                   help="LongMemEval questions to evaluate (default: 50)")
    p.add_argument("--models", default=None,
                   help="Comma-separated rerank models (default: all supported cross-encoders). "
                        "Pass 'none' to run only the OFF baseline.")
    p.add_argument("--out", type=str, default=None,
                   help="Write the JSON results to this path in addition to stdout.")
    args = p.parse_args(argv)

    # Quiet the rerank module's stderr warnings so the table stays clean.
    import os
    os.environ["BRAINCTL_RERANK_QUIET"] = "1"

    # Decide which models to test.
    from agentmemory.rerank import SUPPORTED_MODELS
    if args.models == "none":
        rerank_configs = []
    elif args.models:
        rerank_configs = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        rerank_configs = [
            name for name, spec in SUPPORTED_MODELS.items()
            if spec["kind"] == "cross_encoder"
        ]
        # Still surface qwen3 in the table (as deferred / skipped) so
        # the report shows we considered it.
        rerank_configs.append("qwen3-reranker-4b")

    benches: List[str] = []
    if args.bench == "both":
        benches = ["locomo", "longmemeval"]
    else:
        benches = [args.bench]

    rows: List[Dict[str, Any]] = []
    raw_results: Dict[str, Any] = {}

    # Always-on OFF baseline.
    for bench in benches:
        print(f"[bench] OFF baseline on {bench}...", file=sys.stderr)
        if bench == "locomo":
            res = _eval_locomo(rerank_model=None, n_convos=args.convos)
        else:
            res = _eval_longmemeval(rerank_model=None, n_questions=args.questions)
        rows.append({"config": "OFF (no cross-encoder)", "bench": bench, **res})
        raw_results.setdefault("off", {})[bench] = res

    # Each rerank model.
    for model in rerank_configs:
        from agentmemory.rerank import SUPPORTED_MODELS as _SM
        spec = _SM.get(model)
        if spec and spec["kind"] == "llm_logit":
            for bench in benches:
                rows.append({
                    "config": model + " (deferred — LLM-style)",
                    "bench": bench,
                    "n_questions": "-",
                    "hit_at_1": "-",
                    "hit_at_5": "-",
                    "mrr": "-",
                    "ndcg_at_5": "-",
                    "avg_latency_ms": "-",
                })
            continue

        for bench in benches:
            print(f"[bench] {model} on {bench}...", file=sys.stderr)
            try:
                if bench == "locomo":
                    res = _eval_locomo(rerank_model=model, n_convos=args.convos)
                else:
                    res = _eval_longmemeval(rerank_model=model, n_questions=args.questions)
            except Exception as exc:  # noqa: BLE001
                # Likely sentence-transformers missing or model load failed.
                res = {"error": f"{type(exc).__name__}: {exc}"}
            rows.append({"config": model, "bench": bench, **res})
            raw_results.setdefault(model, {})[bench] = res

    table = _format_table(rows)
    print(table)

    if args.out:
        Path(args.out).write_text(json.dumps({
            "table": rows,
            "raw": raw_results,
        }, indent=2))
        print(f"\n[bench] wrote {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
