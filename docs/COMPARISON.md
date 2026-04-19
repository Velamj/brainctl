# brainctl vs the field

Feature comparison against the six most commonly evaluated agent memory systems.

**Competitors covered:** Mem0, Letta, Zep, Cognee, MemPalace, OpenAI Memory

Research basis: public docs, GitHub repos, and release notes as of April 2026. Rows marked `?` indicate the feature may exist but could not be confirmed from public sources — check the vendor's current docs before relying on this entry. Rows marked `—` indicate the feature is not present based on available documentation.

> **Honesty note on retrieval rows.** brainctl's LOCOMO / LongMemEval numbers are *measured* (Brain.search, default settings, full sweep). Competitor numbers in those rows are *cited* from each project's published material. The same-fixture head-to-head sweep is wired up at `tests/bench/competitor_runs/` (one adapter per system, skip-not-fabricate contract) but has not been executed yet — when it lands, cited numbers get replaced with measured ones.

---

## Feature matrix

| feature | brainctl | Mem0 | Letta | Zep | Cognee | MemPalace | OpenAI Memory |
|---------|----------|------|-------|-----|--------|-----------|---------------|
| **local-first** (no required server) | ✓ | partial¹ | partial² | partial³ | ✓ | ✓ | — |
| **MIT license** | ✓ | — (Apache 2.0) | — (Apache 2.0) | — (proprietary CE deprecated Apr 2025) | — (Apache 2.0) | ? | — (closed) |
| **no LLM calls required** | ✓ | — | — | — | — | ✓ | — |
| **FTS full-text search** | ✓ | — | — | — | — | ? | — |
| **vector / semantic search** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| **hybrid retrieval (FTS + vector)** | ✓ | — | — | — | — | ✓ (hybrid v5) | — |
| **knowledge graph** | ✓ | ✓ (Pro tier) | — | ✓ (Graphiti) | ✓ | ? | — |
| **auto entity linking** | ✓ | — | — | — | — | ? | — |
| **belief revision (AGM)** | ✓ | — | — | — | — | — | — |
| **Ed25519-signed exports** | ✓ | — | — | — | — | — | — |
| **on-chain attestation (Solana)** | ✓ (opt-in) | — | — | — | — | — | — |
| **managed non-custodial wallet** | ✓ | — | — | — | — | — | — |
| **MCP server included** | ✓ (201 tools) | ✓ | ✓ | ✓ | ✓ | ? | — |
| **first-party framework plugins** | 16 | ? | ? | — | — | ? | — |
| **session handoffs** | ✓ | — | ✓ (memory blocks) | — | — | ? | — |
| **prospective memory (triggers)** | ✓ | — | — | — | — | ? | — |
| **multi-agent shared store** | ✓ | ✓ | ✓ | — | — | ? | — |
| **context profiles** | ✓ | — | — | — | — | ? | — |
| **confidence decay / half-life** | ✓ | — | — | — | — | ? | — |
| **write gate (dedup / surprise)** | ✓ | ✓ (conflict detection) | — | — | — | ? | — |
| **consolidation engine** | ✓ | — | — | — | — | ? | — |
| **affect / emotional state tracking** | ✓ | — | — | — | — | ? | — |
| **free at rest (no per-op billing)** | ✓ | partial⁴ | partial⁴ | — | ✓ (local) | ✓ | — |
| **embedding model flexibility** | ✓ (any Ollama model) | ✓ | ✓ | ? | ✓ | ? | — |
| **LoCoMo session-level recall**⁵ | 0.922 | ? | ? | ? | ? | 0.603 | ? |
| **LongMemEval R@5 (n=470)**⁵ | 0.970 | ? | ? | ? | ? | 0.966 | ? |
| **MemBench hit@5 (FirstAgent, n=200)**⁵ | 0.930 | ? | ? | ? | ? | 0.885 | ? |

---

### Footnotes

¹ **Mem0 local**: self-hosted option exists but the knowledge graph (Neo4j) requires a separate server. The free open-source tier uses only vector search; graph features are cloud-only on the Pro plan ($249/mo).

² **Letta local**: self-hosted is supported. Cloud option available. The system requires a running Letta server process — not a single file.

³ **Zep local**: Zep Community Edition was deprecated April 2025. Local operation now requires Graphiti plus a separate graph database (Neo4j, FalkorDB, or Kuzu). Not a single-file deployment.

⁴ **Free at rest (Mem0 / Letta)**: open-source tiers have no per-op billing, but cloud tiers do. For local deployments there is no metering.

⁵ **Same-machine head-to-head, run 2026-04-18.** Hardware: Intel Core Ultra 7 258V, 33.9 GB RAM, Windows 10 Home. Repro: `python benchmarks/compare_memory_engines.py --label full_compare`. Result bundle: `benchmarks/results/full_compare_20260418_033425/`. The LoCoMo row is session-level recall on `locomo10.json` (1,986 QA, brainctl `cmd_session` vs mempalace `raw_session`). The LongMemEval row is R@5 on `longmemeval_s_cleaned.json` (470 q, brainctl `cmd_search` vs mempalace `raw_session`). The MemBench row is hit@5 on the FirstAgent slice (200 q, partial — full sweep pending). ConvoMem was blocked because the evidence payload fetch failed; no fair same-machine number yet.

**Honesty caveat (carried verbatim from the artifact bundle):** the vector-on/off flag for the `cmd_search` run was not persisted into the artifact bundle, so the `cmd_search` numbers above should not be cited as a clean vector-vs-FTS statement without rerunning that exact variant with the flag captured.

---

## Head-to-head retrieval numbers

brainctl's published numbers use the `Brain.search` backend (FTS-only lexical retrieval) and the `cmd_search` backend (full brainctl retrieval pipeline) with default settings. No cherry-picking, no benchmark-specific tuning. Full methodology: [docs/BENCHMARKS.md](../tests/bench/) and the landing page `/benchmarks`.

The 2026-04-18 head-to-head against MemPalace `raw_*` baselines:

| benchmark | scoring | brainctl | mempalace | delta |
|---|---|---|---|---|
| LoCoMo (n=1,986) | session-level avg recall | **0.9217** | 0.6028 | +0.319 |
| LongMemEval (n=470) | R@5 | **0.9702** | 0.9660 | +0.004 |
| LongMemEval (n=470) | R@10 | **0.9894** | 0.9830 | +0.006 |
| MemBench FirstAgent (n=200) | hit@5 | **0.930** | 0.885 | +0.045 |
| ConvoMem | — | blocked | blocked | n/a |

### LoCoMo operating points (brainctl internal, n=1,982)

| metric | turn | session | hybrid | hybrid vs session |
|---|---:|---:|---:|---:|
| Hit@1 | 0.3734 | 0.6731 | 0.6983 | +0.0252 (+3.74%) |
| Hit@5 | 0.6120 | 0.9117 | 0.9132 | +0.0015 (+0.16%) |
| Hit@10 | 0.6892 | 0.9606 | 0.9601 | -0.0005 (-0.05%) |
| MRR | 0.4731 | 0.7749 | 0.7920 | +0.0171 (+2.21%) |
| single-hop Hit@5 | 0.4645 | 0.8688 | 0.8546 | -0.0142 (-1.63%) |
| multi-hop Hit@5 | 0.3696 | 0.6522 | 0.6739 | +0.0217 (+3.33%) |
| temporal Hit@5 | 0.6604 | 0.8972 | 0.8972 | +0.0000 (+0.00%) |

Hybrid is the best overall LoCoMo operating point in this sweep:
higher Hit@1 / Hit@5 / MRR than session, higher multi-hop Hit@5, equal
temporal Hit@5, and a small single-hop Hit@5 giveback.

The full LongMemEval breakdown (R@5 / R@10 / NDCG@5 / NDCG@10) for both `Brain.search` and `cmd_search` is on the `/benchmarks` page.

Rollout posture for top-heavy retrieval is staged/canary-first (I6),
with an explicit rollback switch. Operator controls are
`--rollout-mode`, `--rollout-canary-agents`, `--rollout-canary-percent`,
and `--rollback-top-heavy` (or env equivalents
`BRAINCTL_TOPHEAVY_ROLLOUT_MODE`, `BRAINCTL_TOPHEAVY_CANARY_AGENTS`,
`BRAINCTL_TOPHEAVY_CANARY_PERCENT`, `BRAINCTL_TOPHEAVY_ROLLBACK`).
For provenance, run `brainctl search ... --debug` and inspect `_debug`
keys like `topheavy.rollout_mode`, `topheavy.rollout_reason`,
`topheavy.enabled`, and `<bucket>.cross_encoder_*` / `<bucket>.*_skipped`.

Competitor harness for Mem0 / Letta / Zep / Cognee / OpenAI Memory remains unrun; gated on hosted-API budget for Mem0/Zep and on local-only sweep for Cognee.

---

## What brainctl trades

Honest accounting of the gaps that follow from deliberate choices.

**No managed cloud option.**
brainctl is local-first by design. There is no hosted API, no managed tier, no SaaS dashboard. If your use case requires a shared remote store accessible from multiple machines without manual sync, you'll need to manage `brain.db` replication yourself or pick a different tool.

**LOCOMO baseline is weak on hop-heavy retrieval, though hybrid closes most of it.**
The Brain.search baseline still shows weak single-hop / multi-hop hit@1
(0.167 / 0.174). In the latest LoCoMo sweep, hybrid retrieval improves
rank quality substantially (Hit@1 0.6983, Hit@5 0.9132, MRR 0.7920)
and raises multi-hop Hit@5 to 0.6739, but it still gives back a small
amount of single-hop Hit@5 vs session (-1.63%) and does not improve
Hit@10. The root cause remains benchmark-shape sensitivity: recency and
salience rerankers are less helpful when timestamps are synthetic and
uniform. A `--benchmark` preset is available.

**No real-time multi-machine sync.**
`brain.db` is a single WAL-mode SQLite file. Multi-agent works fine when all agents share the same filesystem. Across machines, you sync the file manually. Zep and Letta's server-based architectures handle distributed access natively.

**Signing requires a Solana keypair.**
The managed wallet command (`brainctl wallet new`) removes the setup friction for non-crypto users, but on-chain pinning still depends on the Solana network. Offline signature verification works without any network — but if you want the on-chain attestation, you're in the Solana ecosystem whether you intended to be or not.

**No UI.**
There is no web dashboard, no graph explorer. Obsidian export gives you a navigable vault layer over the data, but it's a one-way sync. Cognee ships a local graph UI; Letta has a cloud console; Mem0 has a platform dashboard. brainctl is a terminal-first tool.

**Knowledge graph is self-built, not schema-enforced.**
Entities and edges grow organically from memory writes and explicit `entity()` / `relate()` calls. There's no ontology layer, no required schema for relationships. This is flexible but means graph quality depends on what agents write. Cognee's ECL pipeline (Extract, Cognify, Load) is more structured about graph construction.
