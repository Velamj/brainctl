# brainctl vs the field

Feature comparison against the five most commonly evaluated agent memory systems.

**Competitors covered:** Mem0, Letta, Zep, Cognee, OpenAI Memory

Research basis: public docs, GitHub repos, and release notes as of April 2026. Rows marked `?` indicate the feature may exist but could not be confirmed from public sources — check the vendor's current docs before relying on this entry. Rows marked `—` indicate the feature is not present based on available documentation.

---

## Feature matrix

| feature | brainctl | Mem0 | Letta | Zep | Cognee | OpenAI Memory |
|---------|----------|------|-------|-----|--------|---------------|
| **local-first** (no required server) | ✓ | partial¹ | partial² | partial³ | ✓ | — |
| **MIT license** | ✓ | — (Apache 2.0) | — (Apache 2.0) | — (proprietary CE deprecated Apr 2025) | — (Apache 2.0) | — (closed) |
| **no LLM calls required** | ✓ | — | — | — | — | — |
| **FTS full-text search** | ✓ | — | — | — | — | — |
| **vector / semantic search** | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| **hybrid retrieval (FTS + vector)** | ✓ | — | — | — | — | — |
| **knowledge graph** | ✓ | ✓ (Pro tier) | — | ✓ (Graphiti) | ✓ | — |
| **auto entity linking** | ✓ | — | — | — | — | — |
| **belief revision (AGM)** | ✓ | — | — | — | — | — |
| **Ed25519-signed exports** | ✓ | — | — | — | — | — |
| **on-chain attestation (Solana)** | ✓ (opt-in) | — | — | — | — | — |
| **managed non-custodial wallet** | ✓ | — | — | — | — | — |
| **MCP server included** | ✓ (201 tools) | ✓ | ✓ | ✓ | ✓ | — |
| **first-party framework plugins** | 16 | ? | ? | — | — | — |
| **session handoffs** | ✓ | — | ✓ (memory blocks) | — | — | — |
| **prospective memory (triggers)** | ✓ | — | — | — | — | — |
| **multi-agent shared store** | ✓ | ✓ | ✓ | — | — | — |
| **context profiles** | ✓ | — | — | — | — | — |
| **confidence decay / half-life** | ✓ | — | — | — | — | — |
| **write gate (dedup / surprise)** | ✓ | ✓ (conflict detection) | — | — | — | — |
| **consolidation engine** | ✓ | — | — | — | — | — |
| **affect / emotional state tracking** | ✓ | — | — | — | — | — |
| **free at rest (no per-op billing)** | ✓ | partial⁴ | partial⁴ | — | ✓ (local) | — |
| **embedding model flexibility** | ✓ (any Ollama model) | ✓ | ✓ | ? | ✓ | — |
| **LOCOMO hit@1 (overall)** | 0.341 | ? | ? | ? | ? | ? |
| **LongMemEval hit@1 (overall)** | 0.882 | ? | ? | ? | ? | ? |

---

### Footnotes

¹ **Mem0 local**: self-hosted option exists but the knowledge graph (Neo4j) requires a separate server. The free open-source tier uses only vector search; graph features are cloud-only on the Pro plan ($249/mo).

² **Letta local**: self-hosted is supported. Cloud option available. The system requires a running Letta server process — not a single file.

³ **Zep local**: Zep Community Edition was deprecated April 2025. Local operation now requires Graphiti plus a separate graph database (Neo4j, FalkorDB, or Kuzu). Not a single-file deployment.

⁴ **Free at rest (Mem0 / Letta)**: open-source tiers have no per-op billing, but cloud tiers do. For local deployments there is no metering.

---

## LOCOMO + LongMemEval numbers

brainctl's published numbers use the `Brain.search` backend with default settings. No cherry-picking, no benchmark-specific tuning. Full methodology: [docs/BENCHMARKS.md](../tests/bench/) and the landing page `/benchmarks`.

Competitor numbers: not yet available from public sources. Worker A will publish these in v2.4.0.

---

## What brainctl trades

Honest accounting of the gaps that follow from deliberate choices.

**No managed cloud option.**
brainctl is local-first by design. There is no hosted API, no managed tier, no SaaS dashboard. If your use case requires a shared remote store accessible from multiple machines without manual sync, you'll need to manage `brain.db` replication yourself or pick a different tool.

**LOCOMO single-hop and multi-hop are weak.**
hit@1 of 0.167 (single-hop) and 0.174 (multi-hop) are below what you'd expect from a well-tuned retrieval system. The root cause: recency and salience rerankers bias toward recent entries, but LOCOMO's gold evidence is concentrated in early sessions with uniform synthetic timestamps — the rerankers fight the right answer. A `--benchmark` preset is available. Operationally, with real agent data (non-uniform timestamps, natural recency signal), retrieval behaves better — but the benchmark number is the benchmark number.

**No real-time multi-machine sync.**
`brain.db` is a single WAL-mode SQLite file. Multi-agent works fine when all agents share the same filesystem. Across machines, you sync the file manually. Zep and Letta's server-based architectures handle distributed access natively.

**Signing requires a Solana keypair.**
The managed wallet command (`brainctl wallet new`) removes the setup friction for non-crypto users, but on-chain pinning still depends on the Solana network. Offline signature verification works without any network — but if you want the on-chain attestation, you're in the Solana ecosystem whether you intended to be or not.

**No UI.**
There is no web dashboard, no graph explorer. Obsidian export gives you a navigable vault layer over the data, but it's a one-way sync. Cognee ships a local graph UI; Letta has a cloud console; Mem0 has a platform dashboard. brainctl is a terminal-first tool.

**Knowledge graph is self-built, not schema-enforced.**
Entities and edges grow organically from memory writes and explicit `entity()` / `relate()` calls. There's no ontology layer, no required schema for relationships. This is flexible but means graph quality depends on what agents write. Cognee's ECL pipeline (Extract, Cognify, Load) is more structured about graph construction.
