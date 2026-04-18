"""Embedding-model bake-off — runs every model in
``agentmemory.embeddings.EMBEDDING_MODELS`` against the LOCOMO and
LongMemEval retrieval suites, captures Hit@1/Hit@5/MRR/nDCG@5/Recall@5
plus per-memory and per-query embed wall-clock, then writes a comparison
table to ``tests/bench/baselines/embedding_bakeoff.json``.

Why a subprocess per model
==========================

The codebase pins ``EMBED_MODEL`` / ``EMBED_DIMENSIONS`` at module-import
time in five places (``mcp_server.py``, ``mcp_tools_meb.py``,
``mcp_tools_dmem.py``, ``vec.py``, ``_impl.py``). In-process model swaps
fight that caching; the cleanest isolation is one Python subprocess per
model with ``BRAINCTL_EMBED_MODEL`` and ``BRAINCTL_EMBED_DIMENSIONS`` set
in env *before* anything imports brainctl. This file's ``main`` is the
orchestrator; ``--single MODEL`` mode is the per-model worker that runs
inside each subprocess.

Usage
=====

::

    # Pull all models first (~7.4 GB total — see EMBEDDING_MODELS sizes):
    for m in nomic-embed-text bge-m3 mxbai-embed-large \\
              snowflake-arctic-embed2 qwen3-embedding:8b ; do
        ollama pull "$m"
    done

    # Run the full bake-off (writes baselines/embedding_bakeoff.json):
    python -m tests.bench.embedding_bakeoff \\
        --locomo-convo 0 \\
        --longmem-limit 30

    # Skip a model that's misbehaving on this machine:
    python -m tests.bench.embedding_bakeoff --skip qwen3-embedding:8b

    # Re-run a single model in-process (used by the orchestrator):
    python -m tests.bench.embedding_bakeoff --single bge-m3 \\
        --locomo-convo 0 --longmem-limit 30 --output /tmp/bge.json

The brief asks for a Pareto winner (best LOCOMO Hit@5 within 2x baseline
latency); :func:`pick_winner` implements that. The default-model bump
gate (>=3pp Hit@5, <=1.5x latency) is implemented in
:func:`maybe_promote_default` — it prints the exact one-line edit to
make in ``src/agentmemory/embeddings.py`` if the gate fires; we do NOT
auto-apply, by deliberate choice, because this should be a
human-confirmed CHANGELOG-worthy change (orchestrator wires the actual
edit).
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

# These imports are deliberately deferred inside the subprocess `main`
# so the orchestrator (which doesn't need brainctl) keeps a small
# import surface and per-model env vars take effect cleanly.


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT = _REPO_ROOT / "tests" / "bench" / "baselines" / "embedding_bakeoff.json"

# Models in this order: cheapest-to-pull first so the bake-off produces
# *some* data even if the run is interrupted on the heavy qwen3 entry.
DEFAULT_MODELS = (
    "nomic-embed-text",
    "mxbai-embed-large",
    "bge-m3",
    "snowflake-arctic-embed2",
    "qwen3-embedding:8b",
)


# ---------------------------------------------------------------------------
# Ollama probe — used by both orchestrator and worker
# ---------------------------------------------------------------------------


def _ollama_url() -> str:
    return os.environ.get(
        "BRAINCTL_OLLAMA_URL", "http://localhost:11434/api/embed"
    )


def _ollama_tags_url() -> str:
    base = _ollama_url().rsplit("/api/", 1)[0]
    return f"{base}/api/tags"


def _ollama_models_loaded() -> Dict[str, int]:
    """Return ``{model_name: dim}`` for models the Ollama daemon reports."""
    try:
        with urllib.request.urlopen(_ollama_tags_url(), timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception:
        return {}
    out: Dict[str, int] = {}
    for m in data.get("models", []):
        # Ollama doesn't tell us dim from /api/tags; we leave 0 and let
        # the worker probe it via a single-token /api/embed call.
        out[m.get("name", "")] = 0
    return out


def _probe_model(model: str, timeout: float = 60.0) -> Optional[Tuple[int, float]]:
    """Single-token embed against ``model``; returns ``(dim, elapsed_s)``."""
    payload = json.dumps({"model": model, "input": "."}).encode()
    req = urllib.request.Request(
        _ollama_url(), data=payload,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"  [probe] {model}: failed ({exc})", file=sys.stderr)
        return None
    elapsed = time.perf_counter() - t0
    try:
        vec = data["embeddings"][0]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(vec, list) or not vec:
        return None
    return len(vec), elapsed


# ---------------------------------------------------------------------------
# Worker — runs inside each subprocess, scoped to ONE model
# ---------------------------------------------------------------------------


def run_single_model(
    model: str,
    *,
    locomo_convo_idx: Optional[int],
    longmem_limit: Optional[int],
    output_path: Optional[Path],
) -> Dict[str, Any]:
    """Run one model end-to-end. Returns a dict ready to merge into the table.

    Imports of ``agentmemory`` happen here (not at module top) so the
    BRAINCTL_EMBED_MODEL env var the orchestrator sets is in place when
    the registry is read.
    """
    # Sanity: confirm env vars match the model we were told to test.
    env_model = os.environ.get("BRAINCTL_EMBED_MODEL", "")
    if env_model != model:
        print(
            f"WARN: BRAINCTL_EMBED_MODEL={env_model!r} but --single {model!r}; "
            f"forcing env to match.",
            file=sys.stderr,
        )
        os.environ["BRAINCTL_EMBED_MODEL"] = model

    from agentmemory.embeddings import (
        EMBEDDING_MODELS,
        _get_default_embed_model,
        _get_model_dim,
        embed_text,
        warmup_model,
    )

    expected_dim = _get_model_dim(model)
    os.environ["BRAINCTL_EMBED_DIMENSIONS"] = str(expected_dim)
    meta = EMBEDDING_MODELS.get(model, {})

    # Probe — confirms Ollama has the model and gives us the cold-start time.
    probe = _probe_model(model, timeout=180.0)
    if probe is None:
        return {
            "model": model,
            "status": "skipped",
            "reason": "ollama_probe_failed",
            "note": "Run `ollama pull " + meta.get("ollama_tag", model) + "` first.",
        }
    probe_dim, cold_start_s = probe
    if probe_dim != expected_dim:
        return {
            "model": model,
            "status": "skipped",
            "reason": "dim_mismatch",
            "expected_dim": expected_dim,
            "probed_dim": probe_dim,
        }

    # Warm a second call so the per-memory timer measures steady state.
    warmup_model(model)

    out: Dict[str, Any] = {
        "model": model,
        "ollama_tag": meta.get("ollama_tag", model),
        "dim": expected_dim,
        "size_mb": meta.get("size_mb"),
        "cold_start_s": round(cold_start_s, 3),
        "status": "ok",
    }

    # ---- LOCOMO ----
    locomo_block = _run_locomo(model, expected_dim, locomo_convo_idx)
    out["locomo"] = locomo_block

    # ---- LongMemEval ----
    if longmem_limit is None or longmem_limit > 0:
        longmem_block = _run_longmemeval(model, expected_dim, longmem_limit)
    else:
        longmem_block = {"skipped": True, "reason": "longmem_limit=0"}
    out["longmemeval"] = longmem_block

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(out, indent=2, sort_keys=True))
    return out


def _run_locomo(
    model: str,
    expected_dim: int,
    convo_idx: Optional[int],
) -> Dict[str, Any]:
    """LOCOMO ingest + score under the current model.

    Always uses the ``brain`` backend (FTS + vec via ``Brain.search``) —
    the cmd_search reranker chain is orthogonal to the embedding-model
    question and would only add noise. Each model gets its own tmp DB.
    """
    from agentmemory.brain import Brain
    from agentmemory.embeddings import embed_text, pack_embedding
    from tests.bench.datasets.locomo_loader import load as load_locomo
    from tests.bench.locomo_eval import (
        BASELINE_KS,
        conversation_to_questions,
        conversation_to_turns,
        run_conversation,
    )

    data = load_locomo(allow_download=False)
    convos = [data[convo_idx]] if convo_idx is not None else data
    headline_K = 5

    per_convo: List[Dict[str, Any]] = []
    embed_times: List[float] = []
    query_times: List[float] = []
    n_turns_total = 0

    for convo in convos:
        sample_id = convo.get("sample_id", "?")
        questions = conversation_to_questions(convo)
        turns = conversation_to_turns(convo)

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / f"locomo-{sample_id}.db"
            agent_id = f"bakeoff-{model}-{sample_id}"
            brain = Brain(db_path=str(db_path), agent_id=agent_id)

            # Time ingestion at memory granularity.
            t_ingest_start = time.perf_counter()
            n_written = 0
            for turn in turns:
                if not turn.key:
                    continue
                # Time JUST the embed, not the SQL write.
                t0 = time.perf_counter()
                vec = embed_text(turn.text, model=model)
                embed_times.append(time.perf_counter() - t0)
                if vec is None:
                    continue
                # Brain.remember handles its own embed internally — but we
                # still want to use it so the rest of the indexing logic
                # (FTS, KG hooks) fires identically. The above timing call
                # is the steady-state-per-memory measurement.
                from tests.bench.external_runner import format_turn
                brain.remember(format_turn(turn), category="observation")
                n_written += 1
            t_ingest = time.perf_counter() - t_ingest_start
            n_turns_total += n_written

            # Score the QA set.
            from tests.bench.external_runner import (
                brain_search_fn,
                score_question,
            )
            search_fn = brain_search_fn(brain)
            t_query_start = time.perf_counter()
            rows = []
            for q in questions:
                tq = time.perf_counter()
                row = score_question(q, search_fn, k_max=max(BASELINE_KS),
                                     ks=BASELINE_KS)
                query_times.append(time.perf_counter() - tq)
                rows.append(row)
            t_query = time.perf_counter() - t_query_start

            from tests.bench.external_runner import aggregate_results
            agg = aggregate_results(rows, ks=BASELINE_KS)
            per_convo.append({
                "sample_id": sample_id,
                "n_turns": n_written,
                "n_questions": len(rows),
                "t_ingest_s": round(t_ingest, 2),
                "t_query_s": round(t_query, 2),
                **agg,
            })

            try:
                brain.close()
            except Exception:
                pass
            gc.collect()

    # Aggregate across convos with question-weighted means.
    weights = [c["n_questions"] for c in per_convo]
    total_q = sum(weights) or 1
    overall: Dict[str, Any] = {}
    for k in (
        f"hit_at_1", f"hit_at_5", f"hit_at_10",
        "mrr",
        f"recall_at_1", f"recall_at_5",
        f"ndcg_at_5",
    ):
        overall[k] = round(sum(
            c["overall"].get(k, 0.0) * c["n_questions"] for c in per_convo
        ) / total_q, 4)

    return {
        "n_convos": len(per_convo),
        "n_turns_total": n_turns_total,
        "n_questions_total": total_q,
        "headline_K": headline_K,
        "overall": overall,
        "per_convo": per_convo,
        "latency": {
            "embed_per_memory_s_mean": _mean(embed_times),
            "embed_per_memory_s_p50": _pct(embed_times, 50),
            "embed_per_memory_s_p95": _pct(embed_times, 95),
            "query_per_q_s_mean": _mean(query_times),
            "query_per_q_s_p95": _pct(query_times, 95),
            "n_embed_samples": len(embed_times),
            "n_query_samples": len(query_times),
        },
    }


def _run_longmemeval(
    model: str,
    expected_dim: int,
    limit: Optional[int],
) -> Dict[str, Any]:
    """LongMemEval-S retrieval scoring — one Brain per entry.

    The dataset cache lives at
    ``tests/bench/datasets/longmemeval/longmemeval_s_cleaned.json``;
    if it isn't present, this block returns ``skipped`` rather than
    triggering a 277 MB download from inside the bake-off. Pre-cache it
    by running ``python -m tests.bench.run --bench longmemeval`` once,
    or pass a smaller ``--longmem-limit`` if RAM is tight.
    """
    try:
        from tests.bench.datasets.longmemeval_loader import load as load_lme
    except Exception as exc:
        return {"skipped": True, "reason": f"loader_import_failed: {exc}"}

    try:
        entries = load_lme(allow_download=False)
    except Exception as exc:
        return {"skipped": True, "reason": f"dataset_unavailable: {exc}"}

    if not entries:
        return {"skipped": True, "reason": "dataset_empty"}

    # Stratified subset for speed (~2-3 minutes total per model on ~30 entries).
    from tests.bench.longmemeval_eval import _stratified_subset, BASELINE_KS, run_entry
    if limit is not None and limit > 0:
        entries = _stratified_subset(entries, limit)

    rows: List[Dict[str, Any]] = []
    embed_times: List[float] = []
    query_times: List[float] = []

    from agentmemory.embeddings import embed_text

    for e in entries:
        # Quick measurement: embed each session text ourselves to capture
        # per-memory latency, then score via the standard run_entry path.
        for sess in e.get("haystack_sessions", []):
            text = "\n".join(
                f"[{t.get('role','')}] {t.get('content','')}"
                for t in sess if t.get("content")
            )
            if not text:
                continue
            t0 = time.perf_counter()
            embed_text(text[:8192], model=model)  # cap to avoid mxbai 512-token error
            embed_times.append(time.perf_counter() - t0)

        tq = time.perf_counter()
        try:
            row = run_entry(e, backend="brain", ks=BASELINE_KS)
        except Exception as exc:
            row = {}
            print(f"  [longmem] entry failed: {exc}", file=sys.stderr)
        query_times.append(time.perf_counter() - tq)
        if row:
            row.pop("_question_result", None)
            rows.append(row)

    # Aggregate.
    if not rows:
        return {"skipped": True, "reason": "all_entries_failed"}
    overall: Dict[str, Any] = {
        "n_entries": len(rows),
        "hit_at_1": _mean([r.get("hit_at_1", 0) for r in rows]),
        "hit_at_5": _mean([r.get("hit_at_5", 0) for r in rows]),
        "hit_at_10": _mean([r.get("hit_at_10", 0) for r in rows]),
        "mrr": _mean([r.get("mrr", 0) for r in rows]),
        "recall_at_5": _mean([r.get("recall_at_5", 0) for r in rows]),
        "ndcg_at_5": _mean([r.get("ndcg_at_5", 0) for r in rows]),
    }
    return {
        "limit": limit,
        "overall": overall,
        "latency": {
            "embed_per_memory_s_mean": _mean(embed_times),
            "embed_per_memory_s_p50": _pct(embed_times, 50),
            "embed_per_memory_s_p95": _pct(embed_times, 95),
            "query_per_q_s_mean": _mean(query_times),
            "query_per_q_s_p95": _pct(query_times, 95),
            "n_embed_samples": len(embed_times),
            "n_query_samples": len(query_times),
        },
    }


# ---------------------------------------------------------------------------
# Orchestrator — spawns one subprocess per model, aggregates, writes JSON
# ---------------------------------------------------------------------------


def run_all(
    *,
    models: Sequence[str],
    locomo_convo_idx: Optional[int],
    longmem_limit: Optional[int],
    output: Path,
    skip: Sequence[str] = (),
    timeout_per_model_s: int = 1800,
) -> Dict[str, Any]:
    """Spawn ``--single`` subprocesses, collect per-model JSON, write the table."""
    output.parent.mkdir(parents=True, exist_ok=True)
    per_model: List[Dict[str, Any]] = []
    skip_set = set(skip)

    for m in models:
        if m in skip_set:
            print(f"=== skipping {m} (--skip) ===", flush=True)
            per_model.append({"model": m, "status": "skipped",
                              "reason": "user_skip"})
            continue
        print(f"=== running {m} ===", flush=True)

        with tempfile.NamedTemporaryFile(
            mode="r", suffix=f"-{_safe(m)}.json", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            env = dict(os.environ)
            env["BRAINCTL_EMBED_MODEL"] = m
            # The dim is set inside the worker after probing — leave env clean.
            env.pop("BRAINCTL_EMBED_DIMENSIONS", None)
            cmd = [
                sys.executable, "-m", "tests.bench.embedding_bakeoff",
                "--single", m,
                "--output", str(tmp_path),
            ]
            if locomo_convo_idx is not None:
                cmd += ["--locomo-convo", str(locomo_convo_idx)]
            if longmem_limit is not None:
                cmd += ["--longmem-limit", str(longmem_limit)]
            t0 = time.perf_counter()
            try:
                proc = subprocess.run(
                    cmd, env=env, cwd=str(_REPO_ROOT),
                    timeout=timeout_per_model_s,
                    stdout=sys.stdout, stderr=sys.stderr,
                )
            except subprocess.TimeoutExpired:
                per_model.append({
                    "model": m,
                    "status": "skipped",
                    "reason": "timeout",
                    "wall_s": timeout_per_model_s,
                })
                continue
            wall_s = time.perf_counter() - t0

            if proc.returncode != 0:
                per_model.append({
                    "model": m,
                    "status": "skipped",
                    "reason": f"subprocess_failed_rc={proc.returncode}",
                    "wall_s": round(wall_s, 1),
                })
                continue
            try:
                row = json.loads(tmp_path.read_text())
                row["wall_s"] = round(wall_s, 1)
            except Exception as exc:
                per_model.append({
                    "model": m,
                    "status": "skipped",
                    "reason": f"output_parse_failed: {exc}",
                })
                continue
            per_model.append(row)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    # Pareto + summary.
    table = _build_summary_table(per_model)
    winner_block = pick_winner(per_model)

    # Pull current default for the "should we promote?" gate.
    try:
        from agentmemory.embeddings import DEFAULT_MODEL_NAME
        current_default = DEFAULT_MODEL_NAME
    except Exception:
        current_default = "nomic-embed-text"

    promotion = maybe_promote_default(per_model, current_default=current_default)

    payload = {
        "ts": int(time.time()),
        "models_tested": list(models),
        "skipped": list(skip_set),
        "current_default": current_default,
        "summary_table": table,
        "winner": winner_block,
        "promotion": promotion,
        "per_model": per_model,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print()
    _print_table(table, current_default=current_default)
    print()
    if winner_block.get("name"):
        print(
            f"Winner: {winner_block['name']} ({winner_block['why']})"
        )
    if promotion.get("promote"):
        print(
            f"PROMOTE: {promotion['from']} -> {promotion['to']} "
            f"({promotion['rationale']})"
        )
        print(f"  edit: {promotion['edit_hint']}")
    else:
        print(f"DO NOT promote ({promotion.get('rationale', 'no reason')})")
    return payload


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def _mean(xs: Sequence[float]) -> float:
    xs = list(xs)
    return round(statistics.mean(xs), 6) if xs else 0.0


def _pct(xs: Sequence[float], pct: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = max(0, min(len(xs) - 1, int(round((pct / 100) * (len(xs) - 1)))))
    return round(xs[k], 6)


def _build_summary_table(per_model: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in per_model:
        if r.get("status") != "ok":
            rows.append({
                "model": r.get("model"),
                "status": r.get("status", "skipped"),
                "reason": r.get("reason"),
            })
            continue
        loc = r.get("locomo", {}).get("overall", {})
        loc_lat = r.get("locomo", {}).get("latency", {})
        lme = r.get("longmemeval", {}).get("overall", {}) or {}
        lme_lat = r.get("longmemeval", {}).get("latency", {}) or {}
        rows.append({
            "model": r["model"],
            "status": "ok",
            "dim": r.get("dim"),
            "size_mb": r.get("size_mb"),
            "cold_start_s": r.get("cold_start_s"),
            "wall_s": r.get("wall_s"),
            # LOCOMO
            "locomo_hit_at_1": loc.get("hit_at_1"),
            "locomo_hit_at_5": loc.get("hit_at_5"),
            "locomo_mrr": loc.get("mrr"),
            "locomo_ndcg_at_5": loc.get("ndcg_at_5"),
            "locomo_recall_at_5": loc.get("recall_at_5"),
            "locomo_embed_p50_s": loc_lat.get("embed_per_memory_s_p50"),
            "locomo_embed_p95_s": loc_lat.get("embed_per_memory_s_p95"),
            "locomo_query_p95_s": loc_lat.get("query_per_q_s_p95"),
            # LongMemEval
            "lme_hit_at_5": lme.get("hit_at_5"),
            "lme_mrr": lme.get("mrr"),
            "lme_ndcg_at_5": lme.get("ndcg_at_5"),
            "lme_n_entries": lme.get("n_entries"),
            "lme_embed_p50_s": lme_lat.get("embed_per_memory_s_p50"),
        })
    return rows


def _print_table(rows: List[Dict[str, Any]], *, current_default: str) -> None:
    cols = [
        ("model", 28),
        ("status", 8),
        ("dim", 5),
        ("loc_H@5", 8),
        ("loc_MRR", 8),
        ("loc_nDCG@5", 11),
        ("lme_H@5", 8),
        ("lme_MRR", 8),
        ("emb_p50", 8),
        ("cold_s", 7),
        ("size_MB", 7),
    ]
    head = "  ".join(f"{c:<{w}}" for c, w in cols)
    print(head)
    print("-" * len(head))
    for r in rows:
        if r.get("status") != "ok":
            line = f"{r.get('model','?')[:28]:<28}  {r.get('status','?'):<8}  - reason: {r.get('reason','?')}"
            print(line)
            continue
        marker = " *" if r["model"] == current_default else "  "
        cells = [
            (r["model"][:28] + marker)[:28],
            r["status"],
            str(r.get("dim", "?")),
            f"{r.get('locomo_hit_at_5', 0):.4f}",
            f"{r.get('locomo_mrr', 0):.4f}",
            f"{r.get('locomo_ndcg_at_5', 0):.4f}",
            f"{(r.get('lme_hit_at_5') or 0):.4f}" if r.get("lme_hit_at_5") is not None else "n/a",
            f"{(r.get('lme_mrr') or 0):.4f}" if r.get("lme_mrr") is not None else "n/a",
            f"{(r.get('locomo_embed_p50_s') or 0):.3f}",
            f"{(r.get('cold_start_s') or 0):.1f}",
            str(r.get("size_mb", "?")),
        ]
        print("  ".join(f"{c:<{w}}" for c, (_, w) in zip(cells, cols)))


# ---------------------------------------------------------------------------
# Pareto winner logic
# ---------------------------------------------------------------------------


def pick_winner(per_model: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the model that beats LOCOMO Hit@5 hardest at <=2x baseline embed time.

    Pareto, per the brief: "best Hit@5 on LOCOMO without being more than
    2x slower than nomic-embed-text". Baseline = whichever
    nomic-embed-text row is in the data; if it's not present, fall back
    to the first ok-status row's latency.
    """
    ok = [r for r in per_model if r.get("status") == "ok"]
    if not ok:
        return {"name": None, "why": "no ok models"}
    baseline = next((r for r in ok if r["model"] == "nomic-embed-text"), None)
    if baseline is None:
        baseline = ok[0]
    base_lat = (
        baseline.get("locomo", {}).get("latency", {}).get("embed_per_memory_s_p50")
        or 0.0
    )
    if base_lat <= 0:
        # If we couldn't even time the baseline, fall back to "best Hit@5".
        best = max(ok, key=lambda r: r.get("locomo", {}).get("overall", {})
                   .get("hit_at_5", 0.0))
        return {
            "name": best["model"],
            "why": "no baseline latency, picked highest Hit@5",
            "hit_at_5": best.get("locomo", {}).get("overall", {}).get("hit_at_5"),
        }
    cap_lat = 2.0 * base_lat
    candidates = [
        r for r in ok
        if (r.get("locomo", {}).get("latency", {}).get("embed_per_memory_s_p50") or 0.0)
        <= cap_lat
    ]
    if not candidates:
        # Latency cap eliminated everyone — pick baseline.
        return {
            "name": baseline["model"],
            "why": "all candidates exceeded 2x baseline latency",
            "baseline_lat_p50": base_lat,
        }
    best = max(candidates, key=lambda r: r.get("locomo", {}).get("overall", {})
               .get("hit_at_5", 0.0))
    return {
        "name": best["model"],
        "why": (
            f"best LOCOMO Hit@5 within 2x baseline latency "
            f"({base_lat:.3f}s -> cap {cap_lat:.3f}s)"
        ),
        "hit_at_5": best.get("locomo", {}).get("overall", {}).get("hit_at_5"),
        "embed_p50_s": best.get("locomo", {}).get("latency", {})
                          .get("embed_per_memory_s_p50"),
        "baseline": baseline["model"],
        "baseline_hit_at_5": baseline.get("locomo", {}).get("overall", {})
                                .get("hit_at_5"),
        "baseline_lat_p50_s": base_lat,
    }


def maybe_promote_default(
    per_model: List[Dict[str, Any]],
    *,
    current_default: str,
) -> Dict[str, Any]:
    """Decide if we should bump the registry's DEFAULT_MODEL_NAME.

    Gate (per brief): winner beats nomic by >=3pp on LOCOMO Hit@5
    AND <=1.5x latency. This is intentionally tighter than the
    Pareto-winner gate (2x latency) — promoting the default ships the
    cost to *every* user, so the bar is higher.

    Returns a dict; ``promote=True`` means the orchestrator should edit
    ``DEFAULT_MODEL_NAME`` in ``src/agentmemory/embeddings.py``. We do
    not auto-apply — the brief says "Document the change loudly so the
    orchestrator knows to mention it in the CHANGELOG."
    """
    ok = [r for r in per_model if r.get("status") == "ok"]
    baseline = next((r for r in ok if r["model"] == current_default), None)
    if baseline is None:
        return {"promote": False, "rationale": f"current default {current_default} not in results"}

    base_hit = baseline.get("locomo", {}).get("overall", {}).get("hit_at_5", 0.0)
    base_lat = (
        baseline.get("locomo", {}).get("latency", {}).get("embed_per_memory_s_p50")
        or 0.0
    )
    cap_lat = 1.5 * base_lat if base_lat > 0 else float("inf")

    eligible = []
    for r in ok:
        if r["model"] == current_default:
            continue
        h = r.get("locomo", {}).get("overall", {}).get("hit_at_5", 0.0)
        lat = r.get("locomo", {}).get("latency", {}).get("embed_per_memory_s_p50") or 0.0
        if h - base_hit < 0.03:
            continue
        if lat > cap_lat:
            continue
        eligible.append((h, r))
    if not eligible:
        return {
            "promote": False,
            "rationale": (
                f"no candidate beats {current_default} by >=3pp Hit@5 "
                f"with <=1.5x latency (baseline H@5={base_hit:.4f}, "
                f"lat_p50={base_lat:.3f}s)"
            ),
        }
    eligible.sort(reverse=True)
    _, best = eligible[0]
    return {
        "promote": True,
        "from": current_default,
        "to": best["model"],
        "rationale": (
            f"{best['model']} hit_at_5={best['locomo']['overall']['hit_at_5']:.4f} "
            f"vs baseline {base_hit:.4f} "
            f"(+{(best['locomo']['overall']['hit_at_5'] - base_hit) * 100:.1f}pp), "
            f"embed_p50={best['locomo']['latency']['embed_per_memory_s_p50']:.3f}s "
            f"vs baseline {base_lat:.3f}s "
            f"(ratio={best['locomo']['latency']['embed_per_memory_s_p50']/max(0.001, base_lat):.2f}x)"
        ),
        "edit_hint": (
            f'In src/agentmemory/embeddings.py, change '
            f'`DEFAULT_MODEL_NAME = "{current_default}"` to '
            f'`DEFAULT_MODEL_NAME = "{best["model"]}"` and update the '
            f"comment block above DEFAULT_MODEL_NAME with the date + ratio."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--single", default=None,
                   help="Per-model worker mode: run only this model in-process")
    p.add_argument("--models", default=None,
                   help="Comma-separated model list (default: all in registry)")
    p.add_argument("--skip", default="",
                   help="Comma-separated models to skip in orchestrator mode")
    p.add_argument("--locomo-convo", type=int, default=0,
                   help="LOCOMO conversation index to run (default 0; "
                        "set to -1 for all 10)")
    p.add_argument("--longmem-limit", type=int, default=0,
                   help="LongMemEval entries to score (0 = skip; default 0 "
                        "since the dataset is gitignored and may not exist locally)")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT),
                   help=f"Where to write the bake-off JSON (default {DEFAULT_OUTPUT})")
    p.add_argument("--timeout-per-model", type=int, default=1800,
                   help="Per-model wall-clock cap in seconds (default 1800 = 30 min)")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    locomo_idx = None if args.locomo_convo == -1 else args.locomo_convo
    longmem_limit = args.longmem_limit if args.longmem_limit else None

    if args.single:
        out = run_single_model(
            args.single,
            locomo_convo_idx=locomo_idx,
            longmem_limit=longmem_limit,
            output_path=Path(args.output),
        )
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("status") == "ok" else 1

    models = args.models.split(",") if args.models else list(DEFAULT_MODELS)
    skip = [s.strip() for s in args.skip.split(",") if s.strip()]
    payload = run_all(
        models=models,
        locomo_convo_idx=locomo_idx,
        longmem_limit=longmem_limit,
        output=Path(args.output),
        skip=skip,
        timeout_per_model_s=args.timeout_per_model,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
