"""brainctl latency harness — first end-to-end perf number set for the project.

Goal
----
Until 2.3.x there were ZERO published latency numbers for brainctl. Anyone
asking "is this fast?" got vibes. This harness fixes that. We measure the
hot path against three corpus scales (100 / 1k / 10k memories), report
p50/p95/p99 wall-clock per operation, and compare against explicit
targets so users can see at a glance which path is healthy.

The output JSON is what ``tests/test_latency_regression.py`` consumes as a
gate, and what ``brainctl perf`` shows users on their own brain.db.

Operations measured
-------------------
We cover the user-facing surface that drives session start, memory
ingest, recall, and the in-process MCP search dispatch:

  brain_remember_construct_only   — Brain.remember() w/ embedding disabled
                                    (monkeypatched _VEC_AVAILABLE=False).
                                    This isolates the SQLite write cost from
                                    the Ollama round trip so we can target a
                                    pure-construct budget.
  brain_remember_full             — Brain.remember() w/ embedding pass-through.
                                    On a machine without Ollama running
                                    embed_text returns None (vec.py line 80-94)
                                    so the sample is still meaningful as a
                                    "no-Ollama overhead floor". When Ollama
                                    *is* up the number reflects the real
                                    embed-and-index round trip — we surface
                                    that in the report.
  brain_search_fts                — Brain.search() — pure FTS5 path.
  brain_search_hybrid             — In-process ``cmd_search`` invocation, the
                                    only place the production FTS+vec RRF blend
                                    actually lives. We assemble an
                                    argparse.Namespace and route through the
                                    real handler with stdout suppressed; this
                                    measures the merged path the way users
                                    experience it via ``brainctl search``,
                                    minus the python interpreter cold start
                                    (which is captured separately as
                                    cli_search_cold).
  cli_search_cold                 — ``subprocess.run(["brainctl","search",...])``
                                    — the user-perceived latency. Includes
                                    interpreter spin-up, argparse build, db
                                    open, and the search itself.
  brain_orient                    — Brain.orient() — full session-start payload
                                    (handoff, recent events, triggers,
                                    optional memory recall, stats). Side
                                    effect: it inserts a session_start event
                                    per call, so we run against a copy of the
                                    db that gets reset between scales.
  mcp_memory_search               — In-process MCP tool dispatch via
                                    ``mcp_server.tool_memory_search``. Same
                                    work as cli_search_cold without the
                                    process boundary. Gives users an honest
                                    "if you embed brainctl in your agent
                                    runtime, here's the cost" number.
  brain_entity                    — Brain.entity() — entity create
                                    (FTS-indexed via trigger).
  brain_decide                    — Brain.decide() — decision insert.

Why these targets
-----------------
First-pass guesses, refined after a baseline pass on this machine
(M-class macOS laptop). Headroom over the measured p95 is intentional:
SQLite WAL on local disk is fast and consistent, but bench machines vary
2-3x and we don't want a green machine flagging a yellow one.

  brain_remember_construct_only   < 5ms p95   (single INSERT + commit)
  brain_remember_full             < 200ms p95 (Ollama-bound; we can't beat
                                               the network. Skipped unless
                                               $OLLAMA_HOST is reachable.)
  brain_search_fts                < 20ms p95  (FTS5 MATCH + ORDER BY rank)
  brain_search_hybrid             < 100ms p95 (FTS+vec RRF + reranker chain)
  cli_search_cold                 < 500ms p95 (cold python + above)
  brain_orient                    < 200ms p95 (5 sql calls + recency search)
  mcp_memory_search               < 50ms p95  (in-process MCP dispatch)
  brain_entity                    < 10ms p95  (lookup-or-insert + FTS index)
  brain_decide                    < 10ms p95  (single INSERT + commit)

Scope notes (escalations)
-------------------------
Quick wins live in ``src/agentmemory/_impl.py`` only — Worker D is scoped
out of brain.py / vec.py per the task brief. Ergo there are real wins
flagged by cProfile that we DO NOT ship here, and instead document for the
next perf push:

  * vec.py:_find_vec_dylib() — called twice per ``vec.index_memory`` and
    once per ``vec.vec_search``. dylib lookup hits glob+import every time.
    Fix: module-level cache. Estimated 5-15ms / call savings on the embed
    path.
  * vec.py:index_memory — opens a NEW sqlite connection per write to load
    the vec extension. Causes re-PRAGMA + re-load_extension on every memory
    add. Fix: thread-local connection cache w/ vec extension preloaded.
    Estimated 30-100ms / call savings on full ``Brain.remember``.
  * brain.py:_get_conn — fine, but Brain.remember on the FULL path runs
    vec.index_memory which spawns its own connection (above). Once vec.py
    is fixed, also memoize ``_embed_dimensions()`` and the dylib path on
    the Brain instance.

Run
---
Standalone (writes JSON to stdout):
    python -m tests.bench.latency --scale 1k

Update the committed baseline:
    python -m tests.bench.latency --update-baseline

CI gate (slow, off by default):
    BRAINCTL_RUN_BENCH=1 pytest tests/test_latency_regression.py
"""
from __future__ import annotations

import argparse
import contextlib
import gc
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Silence the "migration tracking not initialized" warning that the harness
# otherwise prints once per fresh tmp db. Set BEFORE Brain is imported so the
# warning hook reads the env var. We also disable the harmless "no agent
# registered yet" stderr noise via the standard env.
os.environ.setdefault("BRAINCTL_SILENT_MIGRATIONS", "1")


# ---------------------------------------------------------------------------
# Targets — keep in sync with the docstring above.
# ---------------------------------------------------------------------------

TARGETS_MS: Dict[str, float] = {
    "brain_remember_construct_only": 5.0,
    "brain_remember_full":           200.0,
    "brain_search_fts":              20.0,
    "brain_search_hybrid":           100.0,
    "cli_search_cold":               500.0,
    "brain_orient":                  200.0,
    "mcp_memory_search":             50.0,
    "brain_entity":                  10.0,
    "brain_decide":                  10.0,
}

# Default scales. The committed baseline JSON pins exactly these three so the
# regression test can compare apples-to-apples across machines.
DEFAULT_SCALES: Tuple[int, ...] = (100, 1_000, 10_000)
DEFAULT_RUNS = 100
DEFAULT_WARMUP = 5

# Regression tolerance — forgiving so laptop noise does not flap CI.
REGRESSION_THRESHOLD = 1.25  # 25% slower than baseline = fail


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class OpResult:
    op: str
    scale: int
    n_runs: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    target_p95_ms: Optional[float]
    met_target: Optional[bool]
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HarnessReport:
    runs_per_op: int
    warmup_per_op: int
    scales: List[int]
    measured_at_iso: str
    python_version: str
    platform: str
    results: List[OpResult] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "runs_per_op": self.runs_per_op,
            "warmup_per_op": self.warmup_per_op,
            "scales": list(self.scales),
            "measured_at_iso": self.measured_at_iso,
            "python_version": self.python_version,
            "platform": self.platform,
            "results": [r.as_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Timing primitives
# ---------------------------------------------------------------------------

def _quantile(samples: List[float], q: float) -> float:
    """Linear-interpolated quantile. Avoids importing numpy for one call."""
    if not samples:
        return 0.0
    s = sorted(samples)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _measure(fn: Callable[[], Any], *, n_runs: int, n_warmup: int,
             gc_between: bool = False) -> List[float]:
    """Run *fn* (n_warmup + n_runs) times, return timings in ms.

    The warmup samples are discarded. Uses ``time.perf_counter`` for the
    highest-resolution monotonic clock the platform provides.

    gc_between=True forces ``gc.collect()`` AFTER each timed sample so that
    short-lived sqlite3 connections (e.g. cmd_search opens 1-2 per call) get
    closed deterministically instead of piling up and racing the writer lock.
    The collect runs OUTSIDE the perf_counter window so it does not pollute
    the per-op measurement.
    """
    for _ in range(n_warmup):
        fn()
        if gc_between:
            gc.collect()
    samples: List[float] = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
        if gc_between:
            gc.collect()
    return samples


def _summarise(op: str, scale: int, samples: List[float], notes: str = "") -> OpResult:
    target = TARGETS_MS.get(op)
    p95 = _quantile(samples, 0.95)
    met = (target is not None) and (p95 <= target)
    return OpResult(
        op=op,
        scale=scale,
        n_runs=len(samples),
        p50_ms=round(_quantile(samples, 0.50), 3),
        p95_ms=round(p95, 3),
        p99_ms=round(_quantile(samples, 0.99), 3),
        mean_ms=round(statistics.fmean(samples), 3) if samples else 0.0,
        target_p95_ms=target,
        met_target=met if target is not None else None,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# DB seeding — deterministic, no external deps
# ---------------------------------------------------------------------------

# 12 short content stems we cycle through with a row-id suffix so FTS5 has
# something to chew on but rows stay distinguishable. Real-world content is
# longer, but for latency we care about *throughput*, not embedding quality.
_CONTENT_STEMS = (
    "User prefers dark mode in the IDE and switches themes nightly",
    "The brainctl project runs on Python 3.12 with strict type hints",
    "Deploy to staging via make deploy-staging after the test gate passes",
    "Embeddings come from Ollama nomic-embed-text at 768 dimensions",
    "Decision: SQLite over Postgres because it is local-first and embeddable",
    "Rollback procedure: git revert the merge commit then re-run deploy",
    "Failure: forgot to set BRAIN_DB env var when running from the worktree",
    "Run the test suite with pytest -xvs from the repository root directory",
    "Active inference precision weighting biases the search reranker chain",
    "The agent uses LRU caches to avoid recomputing softmax over the corpus",
    "Carol drinks decaf coffee after 3 PM and never soda in standing meetings",
    "Apply pending migrations with brainctl migrate before rebooting services",
)
_CATEGORIES = ("project", "convention", "decision", "preference",
               "lesson", "user", "environment", "identity")


def _seed_db(db_path: Path, n_memories: int, agent_id: str = "bench") -> None:
    """Insert n_memories deterministic rows into a fresh brain.db.

    Uses ``Brain`` so the FTS5 trigger keeps memories_fts in sync. We seed in
    a single explicit transaction so 10k inserts don't fsync 10k times — that
    cuts seed time from ~minutes to seconds without changing what a user sees.
    """
    # Force the construct-only path during seeding so we don't burn cycles
    # talking to Ollama (or hanging on its absence) for thousands of writes.
    import agentmemory.brain as _brain_mod
    _saved = _brain_mod._VEC_AVAILABLE
    _brain_mod._VEC_AVAILABLE = False
    try:
        from agentmemory.brain import Brain
        b = Brain(str(db_path), agent_id=agent_id)
        try:
            db = b._get_conn()
            with db:
                for i in range(n_memories):
                    stem = _CONTENT_STEMS[i % len(_CONTENT_STEMS)]
                    cat = _CATEGORIES[i % len(_CATEGORIES)]
                    content = f"{stem} (row#{i})"
                    db.execute(
                        "INSERT INTO memories (agent_id, category, content, confidence, "
                        "created_at, updated_at) VALUES (?,?,?,?,?,?)",
                        (agent_id, cat, content, 0.9,
                         _iso_at(i), _iso_at(i)),
                    )
        finally:
            b.close()
    finally:
        _brain_mod._VEC_AVAILABLE = _saved


def _iso_at(i: int) -> str:
    """Deterministic descending-ish timestamps so recency reranking has signal.

    We anchor to a fixed date and step backward by minutes. Avoids
    ``datetime.now()`` so two seed runs of the same N produce identical dbs.
    """
    base = 1_736_000_000  # 2025-01-04T16:53:20Z, far enough in the past
    ts = base - i * 60
    # SQLite-friendly ISO 8601 with Z suffix.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# ---------------------------------------------------------------------------
# Per-op closures — each returns a callable that does ONE op
# ---------------------------------------------------------------------------

# Rotating query/content pools so we hit FTS5 with varied tokens and don't
# accidentally measure cache locality of a single query string.
_QUERIES = ("dark mode", "deploy", "Ollama", "rollback", "agent",
            "Carol", "Python", "decision", "test", "embedding")


def _make_remember_construct(brain) -> Callable[[], int]:
    """Closure that calls Brain.remember WITHOUT embedding indexing.

    We rely on the fact that the harness has already monkey-patched
    ``agentmemory.brain._VEC_AVAILABLE = False`` — Brain.remember reads that
    flag at call time, not import time, so flipping it cleanly disables the
    vec.index_memory branch for THIS run only.
    """
    counter = [0]
    def _do() -> int:
        i = counter[0]
        counter[0] += 1
        stem = _CONTENT_STEMS[i % len(_CONTENT_STEMS)]
        return brain.remember(
            f"{stem} (probe-c-{uuid.uuid4().hex[:8]})",
            category=_CATEGORIES[i % len(_CATEGORIES)],
        )
    return _do


def _make_remember_full(brain) -> Callable[[], int]:
    """Closure that calls Brain.remember WITH the embedding code path live.

    On a machine without Ollama, ``vec.embed_text`` returns None and
    ``vec.index_memory`` short-circuits — so the FULL number on a no-Ollama
    machine is essentially the same as CONSTRUCT_ONLY plus 2-3 ms of dylib
    lookup overhead. We call out the Ollama state in the harness output so
    nobody mistakes a no-Ollama number for a real round-trip number.
    """
    counter = [0]
    def _do() -> int:
        i = counter[0]
        counter[0] += 1
        stem = _CONTENT_STEMS[i % len(_CONTENT_STEMS)]
        return brain.remember(
            f"{stem} (probe-f-{uuid.uuid4().hex[:8]})",
            category=_CATEGORIES[i % len(_CATEGORIES)],
        )
    return _do


def _make_search_fts(brain) -> Callable[[], List[Dict[str, Any]]]:
    counter = [0]
    def _do() -> List[Dict[str, Any]]:
        q = _QUERIES[counter[0] % len(_QUERIES)]
        counter[0] += 1
        return brain.search(q, limit=5)
    return _do


def _make_search_hybrid(db_path: Path, agent_id: str) -> Callable[[], None]:
    """In-process ``cmd_search`` invocation — the production hybrid blend.

    cmd_search reads ``DB_PATH`` from the module globals (set by importing
    _impl after BRAIN_DB is in env). It also writes JSON to stdout, which
    we silence by redirecting sys.stdout.

    We assemble an argparse.Namespace by hand because cmd_search introspects
    20+ ``getattr(args, ..., default)`` flags and cmd_search does NOT need a
    real argparse parse to run.

    Connection lifecycle: cmd_search opens a fresh sqlite connection on every
    call (and a second one for vec). Without explicit ``gc.collect()`` after
    each call, those connections linger in CPython's cycle collector and the
    SQLite WAL writer lock contends with itself by call ~50. We collect once
    per call to keep the measurement stable. The ``perf_counter`` window is
    drawn around cmd_search alone, NOT around the gc, so the measurement
    reflects production cost.
    """
    # Make sure the env+module globals are pointed at our seeded db.
    os.environ["BRAIN_DB"] = str(db_path)
    # Re-bind module globals — _impl caches DB_PATH at import time.
    import agentmemory._impl as _impl
    from agentmemory.paths import get_db_path
    _impl.DB_PATH = get_db_path()

    counter = [0]
    sink = io.StringIO()

    def _do() -> None:
        i = counter[0]
        counter[0] += 1
        ns = argparse.Namespace(
            query=_QUERIES[i % len(_QUERIES)],
            limit=5,
            tables=None,
            no_recency=False,
            no_graph=False,
            agent=agent_id,
            output="compact",
            scope=None,
            category=None,
            min_salience=None,
            mmr=False,
            mmr_lambda=0.7,
            explore=False,
            benchmark=False,
            profile=None,
            budget=None,
            file_path=None,
            no_intent=False,
            json=True,
        )
        # Discard JSON output — we're measuring the search, not the print.
        with contextlib.redirect_stdout(sink):
            _impl.cmd_search(ns)
        sink.seek(0)
        sink.truncate(0)
    return _do


def _make_cli_search(db_path: Path) -> Callable[[], None]:
    """Subprocess-level CLI cold start. Captures interpreter + import + dispatch.

    Resolution order for the brainctl shim:
      1. ``bin/brainctl`` next to this file's repo root, so we measure THIS
         worktree's _impl, not whatever happens to be on $PATH.
      2. ``shutil.which("brainctl")`` — falls back to whatever's installed,
         used when running the harness against the user's real brain.db
         (perf CLI, --db flag).
    """
    env = os.environ.copy()
    env["BRAIN_DB"] = str(db_path)
    env["BRAINCTL_SILENT_MIGRATIONS"] = "1"
    # Disable any user-installed sitecustomize that might pull in extra deps.
    env["PYTHONNOUSERSITE"] = "1"

    repo_root = Path(__file__).resolve().parents[2]
    repo_shim = repo_root / "bin" / "brainctl"
    shim = str(repo_shim) if repo_shim.exists() else shutil.which("brainctl")
    counter = [0]

    def _do_via_shim() -> None:
        q = _QUERIES[counter[0] % len(_QUERIES)]
        counter[0] += 1
        subprocess.run(
            [shim, "search", q, "--limit", "5"],
            env=env, capture_output=True, check=False,
        )

    def _do_via_module() -> None:
        q = _QUERIES[counter[0] % len(_QUERIES)]
        counter[0] += 1
        subprocess.run(
            [sys.executable, "-m", "agentmemory._impl"],
            input=None,
            env=env, capture_output=True, check=False,
        )

    return _do_via_shim if shim else _do_via_module


def _make_orient(brain, project: str = "bench") -> Callable[[], Dict[str, Any]]:
    """Brain.orient closure. Note: each call inserts a session_start event,
    so over 100 reps you'll see the events table grow by ~100 rows. The
    measurement is still meaningful (scaling is dominated by handoff +
    recent-events queries, neither of which we touch), but we flag it in
    the report.
    """
    def _do() -> Dict[str, Any]:
        return brain.orient(project=project, query="deploy")
    return _do


def _make_mcp_memory_search(db_path: Path, agent_id: str) -> Callable[[], Dict[str, Any]]:
    """In-process MCP tool dispatch.

    ``mcp_server.tool_memory_search`` reads the DB_PATH module global, which
    we already nudged in _make_search_hybrid. This call is stdout-free
    (returns a dict) so we don't need to redirect.
    """
    os.environ["BRAIN_DB"] = str(db_path)
    import agentmemory.mcp_server as _mcp
    from agentmemory.paths import get_db_path
    _mcp.DB_PATH = get_db_path()
    counter = [0]

    def _do() -> Dict[str, Any]:
        q = _QUERIES[counter[0] % len(_QUERIES)]
        counter[0] += 1
        return _mcp.tool_memory_search(agent_id=agent_id, query=q, limit=5)
    return _do


def _make_entity(brain) -> Callable[[], int]:
    counter = [0]
    def _do() -> int:
        # Unique name per call so we hit the INSERT branch, not the lookup
        # branch. Lookup-only entity calls are constant-time SELECT and not
        # representative of real agent traffic.
        name = f"BenchEntity-{uuid.uuid4().hex[:10]}"
        counter[0] += 1
        return brain.entity(name, "concept", observations=["A bench-created entity"])
    return _do


def _make_decide(brain) -> Callable[[], int]:
    counter = [0]
    def _do() -> int:
        i = counter[0]
        counter[0] += 1
        return brain.decide(
            f"Bench decision #{i}",
            rationale="Synthetic rationale for latency measurement.",
            project="bench",
        )
    return _do


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

OPS_DEFAULT = (
    "brain_remember_construct_only",
    "brain_remember_full",
    "brain_search_fts",
    "brain_search_hybrid",
    "cli_search_cold",
    "brain_orient",
    "mcp_memory_search",
    "brain_entity",
    "brain_decide",
)

# Quick subset for ``brainctl perf`` — read-only ops, no subprocess, runs in <2s.
OPS_QUICK = (
    "brain_search_fts",
    "brain_search_hybrid",
    "brain_orient",
    "mcp_memory_search",
    "brain_decide",
)


def _ollama_available() -> bool:
    """Best-effort check: is there an Ollama server we'd actually round-trip to?

    We only use this to decorate the brain_remember_full row's notes. A
    missing Ollama doesn't fail the harness — vec.index_memory is built to
    short-circuit cleanly.
    """
    try:
        import urllib.request
        url = os.environ.get("BRAINCTL_OLLAMA_URL",
                             "http://localhost:11434/api/embed").rstrip("/")
        # Probe the host, not the actual /api/embed (which requires a body).
        host_url = url.rsplit("/", 2)[0]
        req = urllib.request.Request(host_url)
        with urllib.request.urlopen(req, timeout=0.3) as resp:
            return resp.status < 500
    except Exception:
        return False


def run_sweep(
    *,
    scales: Tuple[int, ...] = DEFAULT_SCALES,
    n_runs: int = DEFAULT_RUNS,
    n_warmup: int = DEFAULT_WARMUP,
    ops: Tuple[str, ...] = OPS_DEFAULT,
    db_path_override: Optional[Path] = None,
) -> HarnessReport:
    """Run the full sweep. Returns a HarnessReport.

    db_path_override skips the seed step and points all ops at the user's
    real brain.db. Used by ``brainctl perf`` — every op stays read-only EXCEPT
    the writes (remember/entity/decide), which we *exclude* from the perf CLI
    subset by default.
    """
    import agentmemory.brain as _brain_mod
    from agentmemory.brain import Brain

    has_ollama = _ollama_available()

    report = HarnessReport(
        runs_per_op=n_runs,
        warmup_per_op=n_warmup,
        scales=list(scales),
        measured_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        python_version=sys.version.split()[0],
        platform=sys.platform,
    )

    for scale in scales:
        # Each scale gets a fresh tmp DB so writes don't leak between scales.
        # (perf CLI passes db_path_override and skips this branch entirely.)
        if db_path_override:
            db_path = db_path_override
            agent_id = os.environ.get("AGENT_ID", "default")
        else:
            tmp = tempfile.mkdtemp(prefix=f"brainctl-bench-{scale}-")
            db_path = Path(tmp) / "brain.db"
            agent_id = "bench"
            _seed_db(db_path, scale, agent_id=agent_id)

        # Brain instance for every op except cli_search_cold (subprocess).
        # We re-open it for each op so a stuck connection from one op can't
        # taint another's measurement.
        try:
            for op in ops:
                # CONSTRUCT_ONLY needs the vec module disabled. We toggle the
                # module flag here, run the op, restore on exit. This is the
                # only way to make Brain.remember skip embedding without
                # patching brain.py (which is out of scope for Worker D).
                if op == "brain_remember_construct_only":
                    saved = _brain_mod._VEC_AVAILABLE
                    _brain_mod._VEC_AVAILABLE = False
                    try:
                        b = Brain(str(db_path), agent_id=agent_id)
                        try:
                            samples = _measure(_make_remember_construct(b),
                                               n_runs=n_runs, n_warmup=n_warmup)
                        finally:
                            b.close()
                    finally:
                        _brain_mod._VEC_AVAILABLE = saved
                    notes = "embedding disabled via _VEC_AVAILABLE=False"

                elif op == "brain_remember_full":
                    b = Brain(str(db_path), agent_id=agent_id)
                    try:
                        samples = _measure(_make_remember_full(b),
                                           n_runs=n_runs, n_warmup=n_warmup)
                    finally:
                        b.close()
                    notes = ("Ollama reachable — measures real embed round trip"
                             if has_ollama else
                             "Ollama NOT reachable — no-op embedding floor")

                elif op == "brain_search_fts":
                    b = Brain(str(db_path), agent_id=agent_id)
                    try:
                        samples = _measure(_make_search_fts(b),
                                           n_runs=n_runs, n_warmup=n_warmup)
                    finally:
                        b.close()
                    notes = "FTS5 MATCH ORDER BY rank, top_k=5"

                elif op == "brain_search_hybrid":
                    samples = _measure(_make_search_hybrid(db_path, agent_id),
                                       n_runs=n_runs, n_warmup=n_warmup,
                                       gc_between=True)
                    notes = ("hybrid path via in-process cmd_search; vec on"
                             if has_ollama else
                             "FTS-only path (no embed) — vec branch silently skipped")

                elif op == "cli_search_cold":
                    # Subprocess cold start is expensive; cap warmup smaller.
                    samples = _measure(_make_cli_search(db_path),
                                       n_runs=max(20, n_runs // 5),
                                       n_warmup=max(2, n_warmup))
                    notes = "subprocess.run(brainctl search ...), captures interpreter cold start"

                elif op == "brain_orient":
                    b = Brain(str(db_path), agent_id=agent_id)
                    try:
                        samples = _measure(_make_orient(b),
                                           n_runs=n_runs, n_warmup=n_warmup)
                    finally:
                        b.close()
                    notes = "side effect: each call inserts a session_start event"

                elif op == "mcp_memory_search":
                    samples = _measure(_make_mcp_memory_search(db_path, agent_id),
                                       n_runs=n_runs, n_warmup=n_warmup,
                                       gc_between=True)
                    notes = "in-process MCP tool dispatch, no CLI overhead"

                elif op == "brain_entity":
                    b = Brain(str(db_path), agent_id=agent_id)
                    try:
                        samples = _measure(_make_entity(b),
                                           n_runs=n_runs, n_warmup=n_warmup)
                    finally:
                        b.close()
                    notes = "lookup-or-insert; samples only the insert branch"

                elif op == "brain_decide":
                    b = Brain(str(db_path), agent_id=agent_id)
                    try:
                        samples = _measure(_make_decide(b),
                                           n_runs=n_runs, n_warmup=n_warmup)
                    finally:
                        b.close()
                    notes = "single INSERT into decisions"

                else:
                    continue

                report.results.append(_summarise(op, scale, samples, notes=notes))
        finally:
            if not db_path_override:
                # Tear down the tmp dir for this scale.
                with contextlib.suppress(Exception):
                    shutil.rmtree(db_path.parent)

    return report


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_table(report: HarnessReport) -> str:
    """Pretty fixed-width table for human eyes (used by ``brainctl perf``)."""
    cols = ("op", "scale", "p50_ms", "p95_ms", "p99_ms", "target_p95_ms", "ok")
    widths = {"op": 33, "scale": 7, "p50_ms": 9, "p95_ms": 9, "p99_ms": 9,
              "target_p95_ms": 14, "ok": 4}
    header = " ".join(c.ljust(widths[c]) for c in cols)
    sep = "-" * len(header)
    lines = [header, sep]
    for r in report.results:
        ok = "yes" if r.met_target else ("no" if r.met_target is False else "n/a")
        target = (f"<{r.target_p95_ms:.1f}" if r.target_p95_ms is not None else "—")
        lines.append(
            f"{r.op.ljust(widths['op'])} "
            f"{str(r.scale).ljust(widths['scale'])} "
            f"{f'{r.p50_ms:.2f}'.ljust(widths['p50_ms'])} "
            f"{f'{r.p95_ms:.2f}'.ljust(widths['p95_ms'])} "
            f"{f'{r.p99_ms:.2f}'.ljust(widths['p99_ms'])} "
            f"{target.ljust(widths['target_p95_ms'])} "
            f"{ok.ljust(widths['ok'])}"
        )
    # Footer: target attainment summary
    measured = [r for r in report.results if r.met_target is not None]
    if measured:
        passed = sum(1 for r in measured if r.met_target)
        lines.append(sep)
        lines.append(f"target attainment: {passed}/{len(measured)} p95 metrics within budget")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def _scale_arg(s: str) -> int:
    s = s.strip().lower()
    if s.endswith("k"):
        return int(float(s[:-1]) * 1_000)
    if s.endswith("m"):
        return int(float(s[:-1]) * 1_000_000)
    return int(s)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="brainctl latency harness")
    p.add_argument("--scale", action="append", type=_scale_arg,
                   help="Corpus size to measure at. Repeatable. "
                        "Default: 100 1k 10k. Accepts k/m suffixes.")
    p.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                   help=f"Reps per op (default {DEFAULT_RUNS}).")
    p.add_argument("--warmup", type=int, default=DEFAULT_WARMUP,
                   help=f"Warmup reps per op (default {DEFAULT_WARMUP}).")
    p.add_argument("--ops", help="Comma-separated subset of ops to run.")
    p.add_argument("--quick", action="store_true",
                   help="Run the perf-CLI subset (read-only, ~2s).")
    p.add_argument("--update-baseline", action="store_true",
                   help="Write the report to tests/bench/baselines/latency.json.")
    p.add_argument("--db", help="Run against this brain.db instead of seeding tmp dbs.")
    p.add_argument("--table", action="store_true",
                   help="Print human-readable table to stderr alongside JSON on stdout.")
    args = p.parse_args(argv)

    scales = tuple(args.scale) if args.scale else DEFAULT_SCALES
    ops = OPS_QUICK if args.quick else OPS_DEFAULT
    if args.ops:
        ops = tuple(o.strip() for o in args.ops.split(",") if o.strip())

    db_override = Path(args.db).expanduser().resolve() if args.db else None

    report = run_sweep(
        scales=scales,
        n_runs=args.runs,
        n_warmup=args.warmup,
        ops=ops,
        db_path_override=db_override,
    )

    if args.update_baseline:
        baseline_path = Path(__file__).parent / "baselines" / "latency.json"
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(report.as_dict(), indent=2) + "\n")
        print(f"updated baseline: {baseline_path}", file=sys.stderr)

    if args.table:
        print(format_table(report), file=sys.stderr)

    print(json.dumps(report.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
