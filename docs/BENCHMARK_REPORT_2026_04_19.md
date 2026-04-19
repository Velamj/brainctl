# Benchmark Report (for documentation updates)

Date: 2026-04-19
Owner: paperclip-codex
Scope: LongMemEval (289-q table provided), LoCoMo turn/session/hybrid retrieval table, and published `cmd_session` LoCoMo avg-recall head-to-head row.

## 1) Executive summary

- LongMemEval is mixed: top-5 coverage improved, deep-recall ceilings held, but rank-quality metrics regressed.
- LoCoMo is strong for hybrid retrieval: hybrid beats session on Hit@1, Hit@5, MRR, and multi-hop Hit@5; ties temporal Hit@5.
- Head-to-head positioning remains strong: `cmd_session` LoCoMo avg recall is 0.9217 vs MemPalace 0.6028 (+0.3189 absolute, +52.90% relative).

Recommended public framing:
- "Hybrid retrieval improves LoCoMo rank quality while preserving near-ceiling LongMemEval coverage."
- "brainctl cmd_session reaches 0.9217 LoCoMo avg recall in same-machine head-to-head."

## 2) Inputs and provenance

### User-provided input tables

- LongMemEval table (n=289): `old FTS-only baseline` vs `final locked`.
- LoCoMo table (`turn`, `session`, `hybrid`) with Hit@1/5/10, MRR, and category Hit@5 rows.

### Repo-verified artifacts used to validate LoCoMo values

- `/Users/r4vager/agentmemory/tests/bench/locomo/e2e_results/summary-20260419-073929.json` (turn)
- `/Users/r4vager/agentmemory/tests/bench/locomo/e2e_results/summary-20260419-074713.json` (session)
- `/Users/r4vager/agentmemory/tests/bench/locomo/e2e_results/summary-20260419-074728.json` (hybrid)
- `/Users/r4vager/agentmemory/README.md` and `/Users/r4vager/agentmemory/docs/COMPARISON.md` for `cmd_session` avg-recall head-to-head row.

## 3) LongMemEval (289 questions): old FTS-only vs final locked

| Metric | Old | Final | Abs Delta | Rel Delta |
|---|---:|---:|---:|---:|
| Hit@1 | 0.8824 | 0.8685 | -0.0139 | -1.58% |
| Hit@5 | 0.9758 | 0.9792 | +0.0034 | +0.35% |
| Hit@10 | 0.9896 | 0.9896 | +0.0000 | +0.00% |
| Hit@20 | 1.0000 | 1.0000 | +0.0000 | +0.00% |
| MRR | 0.9241 | 0.9147 | -0.0094 | -1.02% |
| nDCG@5 | 0.8910 | 0.8815 | -0.0095 | -1.07% |
| Recall@5 | 0.9217 | 0.9158 | -0.0059 | -0.64% |

Count intuition at n=289:
- Hit@5 gain is roughly +0.98 question (about +1 question in top-5).
- Hit@1 drop is roughly -4.02 questions.

Takeaway:
- Coverage stays elite (Hit@10/20 unchanged at 0.9896/1.0), but first-result ranking quality declined.

## 4) LoCoMo: turn vs session vs hybrid (retrieval)

Using repo-verified values (full precision from artifact summaries):

| Metric | turn | session | hybrid | Hybrid vs Session | Hybrid vs Turn |
|---|---:|---:|---:|---:|---:|
| Hit@1 | 0.3734 | 0.6731 | 0.6983 | +0.0252 (+3.74%) | +0.3249 (+87.01%) |
| Hit@5 | 0.6120 | 0.9117 | 0.9132 | +0.0015 (+0.16%) | +0.3012 (+49.22%) |
| Hit@10 | 0.6892 | 0.9606 | 0.9601 | -0.0005 (-0.05%) | +0.2709 (+39.31%) |
| MRR | 0.4731 | 0.7749 | 0.7920 | +0.0171 (+2.21%) | +0.3189 (+67.41%) |
| single-hop Hit@5 | 0.4645 | 0.8688 | 0.8546 | -0.0142 (-1.63%) | +0.3901 (+84.00%) |
| multi-hop Hit@5 | 0.3696 | 0.6522 | 0.6739 | +0.0217 (+3.33%) | +0.3043 (+82.33%) |
| temporal Hit@5 | 0.6604 | 0.8972 | 0.8972 | +0.0000 (+0.00%) | +0.2368 (+35.86%) |

Count intuition at n=1982:
- Hybrid vs Session: ~+50 at Hit@1, ~+3 at Hit@5, ~-1 at Hit@10.
- Hybrid vs Turn: ~+644 at Hit@1, ~+597 at Hit@5.

Category count intuition:
- Single-hop count=282: hybrid vs session is ~-4 hits at @5.
- Multi-hop count=92: hybrid vs session is ~+2 hits at @5.
- Temporal count=321: tie.

Takeaway:
- Hybrid is the best overall LoCoMo operating point in this set.
- The only meaningful giveback vs session is single-hop @5.

## 5) LoCoMo head-to-head avg recall (`cmd_session`)

Published same-machine row (n=1,986):
- brainctl `cmd_session` avg recall: 0.9217
- MemPalace `raw_session` avg recall: 0.6028
- Delta: +0.3189 absolute, +52.90% relative

This remains your strongest competitive headline metric.

## 6) What to say in docs (recommended copy)

### Headline copy

- "On LoCoMo retrieval, hybrid mode leads on Hit@1, Hit@5, MRR, and multi-hop Hit@5 while tying temporal performance."
- "On LongMemEval (289q), we improved Hit@5 and preserved Hit@10/20 ceilings; Hit@1/MRR/nDCG@5 saw modest regressions."
- "In same-machine head-to-head, brainctl cmd_session reaches 0.9217 LoCoMo avg recall vs 0.6028 for MemPalace."

### Honesty/caveat copy

- "LongMemEval gains are concentrated in top-5 coverage, not first-rank precision."
- "Hybrid slightly trails session on single-hop Hit@5 and is effectively tied on Hit@10."
- "Do not frame this table as a pure vector-on/off claim unless the run persists vector flag provenance."

## 7) Suggested edit targets

1. `/Users/r4vager/agentmemory/README.md`
- In `Retrieval benchmarks`, add a "Latest lock" subtable with this LongMemEval old vs final delta view.
- Keep the existing head-to-head row for cmd_session avg recall.

2. `/Users/r4vager/agentmemory/docs/COMPARISON.md`
- Add a short "LoCoMo hybrid vs session" table (7 rows above) under head-to-head section.
- In "What brainctl trades", avoid stale wording that only reflects old weak single-hop/multi-hop baselines without mentioning hybrid improvements.

3. Landing `/benchmarks` page data source
- Ensure numbers displayed for turn/session/hybrid align with:
  - `summary-20260419-073929.json` (turn)
  - `summary-20260419-074713.json` (session)
  - `summary-20260419-074728.json` (hybrid)

## 8) Claims checklist before publish

- Verify sample size labels are explicit (LongMemEval n=289 table vs head-to-head LongMemEval n=470 rows).
- Label metric families clearly (Hit@k vs Recall@k vs avg recall).
- Keep absolute and relative deltas together for competitive rows.
- Keep one caveat line near every "winner" claim to preserve trust.

---

If you want, I can do the next step and patch `README.md` and `docs/COMPARISON.md` directly with this exact wording.
