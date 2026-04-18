# External benchmarks

Two third-party benchmarks gated against committed baselines so future
regressions are caught in CI:

| Bench | Dataset | License | Size | Headline metrics |
|---|---|---|---|---|
| **LOCOMO** | snap-research/locomo | MIT | 2.7 MB (in-tree) | Hit@1, Hit@5, MRR, nDCG@5, Recall@5 — overall + per category (single-hop, temporal, multi-hop, open-domain, adversarial) |
| **LongMemEval** | xiaowu0162/longmemeval-cleaned (`longmemeval_s_cleaned.json`) | MIT | 264 MB (cached, gitignored) | Hit@1, Hit@5, MRR, nDCG@5, Recall@5 — overall + per axis (single-session-{user,assistant,preference}, multi-session) |

Both are **retrieval-only** evals — no LLM judge, no API budget required.
Each conversation/entry is ingested into a fresh tmp `brain.db`, then every
question is searched and ranked against gold IDs.

A third bench (HotPotQA) was considered and explicitly skipped — see
[Why not HotPotQA?](#why-not-hotpotqa) below.

## Run them

```bash
# LOCOMO — full sweep, FTS5-only Brain.search backend
python3 -m tests.bench.run --bench locomo --backend brain

# LOCOMO — full sweep, hybrid cmd_search backend
python3 -m tests.bench.run --bench locomo --backend cmd

# LOCOMO — single conversation (smoke test, ~13s)
python3 -m tests.bench.run --bench locomo --backend brain --convo 0

# LongMemEval — full sweep (289 retrieval-friendly entries)
python3 -m tests.bench.run --bench longmemeval

# LongMemEval — 50-entry stratified subset (CI default, ~6s)
python3 -m tests.bench.run --bench longmemeval --limit 50

# Regression-gate: compare current run to committed baseline, exit 1 on >2% drop
python3 -m tests.bench.run --bench locomo --backend cmd --check
python3 -m tests.bench.run --bench longmemeval --check

# Refresh a baseline after an intentional improvement
python3 -m tests.bench.run --bench locomo --backend cmd --update-baseline
python3 -m tests.bench.run --bench longmemeval --update-baseline
```

## Run as pytest

The CI test files `tests/test_locomo_bench.py` and
`tests/test_longmemeval_bench.py` mirror `tests/test_search_quality_bench.py`
but are **skipped by default** because they need the dataset and run
slower than the rest of the unit-test suite.

```bash
# Run everything bench-related (LOCOMO smoke + LongMemEval subset)
BRAINCTL_RUN_BENCH=1 pytest tests/test_locomo_bench.py tests/test_longmemeval_bench.py

# Run only LOCOMO
BRAINCTL_RUN_BENCH=locomo pytest tests/test_locomo_bench.py

# Run only LongMemEval
BRAINCTL_RUN_BENCH=longmemeval pytest tests/test_longmemeval_bench.py

# Run the FULL LOCOMO sweep (~270s on FTS5, ~130s on cmd) instead of one convo
BRAINCTL_RUN_BENCH=1 BRAINCTL_BENCH_FULL=1 pytest tests/test_locomo_bench.py

# Run the FULL LongMemEval sweep (~30s) instead of the 50-entry subset
BRAINCTL_RUN_BENCH=1 BRAINCTL_BENCH_FULL=1 pytest tests/test_longmemeval_bench.py
```

The CI tests are pinned to `Brain.search` (FTS5-only) backend on purpose —
the committed `baselines/locomo.json` and `baselines/longmemeval.json` are
captured against that backend, and switching at test time would diff
against the wrong reference. To gate the hybrid `cmd_search` path, run
the CLI directly: `python3 -m tests.bench.run --bench locomo --backend cmd
--check`.

## Datasets

### LOCOMO

* **Source.** `tests/bench/locomo/locomo10.json` is committed (2.7 MB,
  10 conversations × ~600 turns × ~200 QA each). `tests/bench/datasets/locomo_loader.py`
  prefers the in-tree copy first, then falls back to a gitignored cache
  at `tests/bench/datasets/locomo/locomo10.json`, then downloads from
  `https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json`
  (or the HF mirror) as a last resort.
* **Format.** Each conversation is ingested as one memory per dialogue
  turn, with content `"[<speaker> @ <date>] <text> [key=D<session>:<turn>]"`.
  The trailing `[key=...]` marker lets the eval map FTS5 results back to
  LOCOMO's `dia_id` evidence list.
* **Categories.** `1=single-hop, 2=temporal, 3=multi-hop, 4=open-domain, 5=adversarial`
  (inferred from the LOCOMO paper / repo conventions).
* **License.** MIT (https://github.com/snap-research/locomo/blob/main/LICENSE).

### LongMemEval

* **Source.** Downloaded on first run from
  `https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json`
  (~277 MB upstream, ~264 MB unzipped) and cached in
  `tests/bench/datasets/longmemeval/longmemeval_s_cleaned.json`. The
  cache directory is gitignored.
* **Why `_s` and not `_oracle`?** The 15.4 MB `oracle` split has
  `set(haystack_session_ids) == set(answer_session_ids)` for all 500
  entries — i.e. no distractor sessions, so retrieval scores against it
  are vacuously perfect. We verified this empirically before pivoting.
  The `_s` split keeps a 50-session distractor haystack per entry
  (~2% gold/haystack ratio).
* **Format.** Each entry is a question against ~40-60 conversation
  *sessions*, of which 1-3 are gold. We ingest one memory per session
  (joining its turns into a single document) and score retrieval against
  `answer_session_ids`. Coarsening to session-level matches the
  benchmark's evaluation contract exactly.
* **Retrieval-friendly axes.** `single-session-user`,
  `single-session-assistant`, `single-session-preference`, `multi-session`
  are scored by default — their gold answers are deterministic strings
  the conversation contains directly. `temporal-reasoning` and
  `knowledge-update` need an LLM judge for end-to-end accuracy and are
  excluded from the headline number; pass `--include-judge-only` to
  measure their retrieval quality anyway.
* **License.** MIT (https://github.com/xiaowu0162/LongMemEval/blob/main/LICENSE).
* **Network requirement.** Set `BRAINCTL_BENCH_NO_DOWNLOAD=1` to disable
  the auto-download fallback (the bench will then `FileNotFoundError`
  instead of fetching).

## Baselines

| File | Captured against | Purpose |
|---|---|---|
| `baselines/locomo.json` | `--backend brain` (FTS5-only Brain.search) on full 10-convo sweep | Primary CI gate. The Brain.search path is what `tests/test_locomo_bench.py` exercises by default. |
| `baselines/locomo_pre_fix_2026_04_18.json` | `--backend cmd` (hybrid cmd_search) on full sweep, captured against current `main` HEAD before Worker A's reranker fix | Historical record of the broken hybrid path. Orchestrator diffs this vs the post-fix `--backend cmd` run to report headline delta. |
| `baselines/longmemeval.json` | Full 289-entry retrieval-friendly sweep on `--backend brain` | Source-of-truth full-sweep baseline. Used by `BRAINCTL_BENCH_FULL=1 pytest tests/test_longmemeval_bench.py`. |
| `baselines/longmemeval_subset50.json` | 50-entry stratified subset on `--backend brain` | CI default — what `tests/test_longmemeval_bench.py` checks against without `BRAINCTL_BENCH_FULL=1`. Wider 5% tolerance (vs 2% on the full sweep) accommodates the smaller per-axis cells. |

### Headline numbers as captured 2026-04-18

**LOCOMO — Brain.search (FTS5-only), full sweep, 1982 questions across 10 convos, 267 s wall**

| metric | overall | single-hop | temporal | multi-hop | open-domain | adversarial |
|---|---|---|---|---|---|---|
| Hit@1 | 0.3406 | 0.1667 | 0.4050 | 0.1739 | 0.3734 | 0.3767 |
| Hit@5 | 0.5716 | 0.4291 | 0.6480 | 0.3152 | 0.6017 | 0.6031 |
| MRR | 0.4447 | 0.2821 | 0.5103 | 0.2323 | 0.4791 | 0.4794 |
| nDCG@5 | 0.4365 | – | – | – | – | – |
| Recall@5 | 0.5225 | 0.2039 | 0.6150 | 0.2207 | 0.5878 | 0.5964 |

**LOCOMO — cmd_search (hybrid, pre-fix baseline), full sweep, 133 s wall**

| metric | overall | single-hop | temporal | multi-hop | open-domain | adversarial |
|---|---|---|---|---|---|---|
| Hit@1 | 0.0232 | 0.0497 | 0.0810 | 0.0652 | 0.0000 | 0.0000 |
| Hit@5 | 0.0424 | 0.1241 | 0.1215 | 0.0870 | 0.0024 | 0.0000 |
| MRR | 0.0317 | 0.0842 | 0.0980 | 0.0758 | 0.0009 | 0.0000 |
| nDCG@5 | 0.0262 | 0.0487 | 0.0994 | 0.0622 | 0.0007 | 0.0000 |
| Recall@5 | 0.0294 | 0.0497 | 0.1160 | 0.0652 | 0.0012 | 0.0000 |

The cmd vs brain delta on Hit@5 is **0.5716 vs 0.0424 (~13.5x)**, matching
the qualitative finding in `tests/bench/locomo/README.md` that the
hybrid pipeline's recency / salience reranking actively scrambles the
FTS ranking on LOCOMO's synthetic uniform-timestamp corpus. Worker A's
reranker fix on `2.3.1/reranker-gap` should close most of this gap.

**LongMemEval — Brain.search, full retrieval-friendly split, 289 entries, ~30 s wall**

| metric | overall | single-session-user | single-session-assistant | single-session-preference | multi-session |
|---|---|---|---|---|---|
| Hit@1 | 0.8824 | 0.9000 | 1.0000 | 0.5000 | 0.9098 |
| Hit@5 | 0.9758 | 1.0000 | 1.0000 | 0.8333 | 0.9850 |
| MRR | 0.9241 | 0.9348 | 1.0000 | 0.6709 | 0.9436 |
| nDCG@5 | 0.8910 | 0.9508 | 1.0000 | 0.6978 | 0.8573 |
| Recall@5 | 0.9217 | 1.0000 | 1.0000 | 0.8333 | 0.8674 |

`single-session-preference` is the hardest axis (preferences are short
and lexically generic — "I prefer X" against many sessions of distractor
content). The other three saturate the FTS5 path because the gold
session contains the answer string verbatim.

## Comparing to published competitors

LOCOMO's published numbers (Mem0 / Letta / Zep) are end-to-end QA
accuracy under a GPT-judge. The retrieval-only numbers here are an
**upper bound** on what an end-to-end run could achieve — adding a
generator + judge can only make the numbers go down (the generator
might pick the wrong gold turn even when retrieval found it). To get
end-to-end numbers, plug a generator into `score_question` (in
`external_runner.py`) and add a judge call; the retrieval scaffolding
already isolates that work.

## Why not HotPotQA?

The orchestrator scope mentioned HotPotQA as an alternative. We picked
LongMemEval over HotPotQA because:

* **Domain fit.** LongMemEval is purpose-built for *agent long-term
  memory* — same shape as brainctl's actual use case. HotPotQA is a
  Wikipedia multi-hop QA benchmark; the questions are factoid lookups
  in a flat document corpus, which doesn't exercise the
  conversation-as-memory ingest path.
* **Per-axis breakdown.** LongMemEval's 6 axes map cleanly onto memory
  abilities (extraction, multi-session reasoning, knowledge updates,
  temporal reasoning, abstention). That's the dimension a brainctl
  contributor needs in a regression report.
* **License & format consistency.** Both LongMemEval and LOCOMO are
  MIT-licensed and ship as JSON; sharing the loader+eval pattern in
  `tests/bench/datasets/` and `tests/bench/external_runner.py` is
  cheap. HotPotQA ships in JSONL with a different schema and would
  need a separate adapter for marginal added signal.

## Adding a new external bench

1. Drop a loader at `tests/bench/datasets/<bench>_loader.py` that
   exposes a `load(allow_download=...)` callable returning the parsed
   dataset.
2. Drop an eval module at `tests/bench/<bench>_eval.py` that exposes a
   `run(*, backend, ...)` callable returning a dict shaped like
   `{"overall": {...}, "by_category": {...}, "elapsed_s": ..., ...}`.
   Reuse `tests.bench.external_runner.{Turn, Question, ingest_conversation_into_brain, eval_questions}`
   for the plumbing.
3. Add a dispatch branch in `tests/bench/run.py::_build_parser`'s
   `--bench` choices and a `_run_<bench>` function mirroring
   `_run_locomo`.
4. Capture the baseline once (`--update-baseline`) and commit it under
   `tests/bench/baselines/<bench>.json`.
5. Add `tests/test_<bench>_bench.py` mirroring `tests/test_locomo_bench.py`
   (env-gated by `BRAINCTL_RUN_BENCH=1`).
6. Update this README's table.

The `external_runner` exposes:

```python
from tests.bench.external_runner import (
    Turn, Question,                          # input shapes
    ingest_conversation_into_brain,          # bulk-load turns into a Brain
    brain_search_fn,                         # wrap Brain.search into SearchFn
    score_question, eval_questions,          # per-question + per-set scoring
    aggregate_results,                       # overall + per-category rollup
)
```

so a new bench is typically ~150 lines of dataset-specific glue + a
~30-line CI test fixture.
