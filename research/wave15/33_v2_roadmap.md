# brainctl v2.0 Research Roadmap

**Research wave:** 15
**Date:** 2026-04-16
**Status:** Research complete, design pending
**Scope:** Three pillars — auto entity linking, quantum integration,
next-gen capabilities — backed by ~45 papers from the three research
agents plus the existing quantum corpus.

---

## Executive Summary

v1.7–v1.9 shipped 21 neuroscience-grounded features backed by 75
papers. The single biggest finding from production deployment is that
**75% of episodic memories have zero knowledge-graph connections** —
the coupling gate rejects them from promotion, quantum interference
has no edges to work with, and PageRank spreading activation is
starved. v2.0 addresses this structural gap first, then builds on
the now-populated graph.

### Three Pillars

1. **Auto Entity Linking** — fix the 75% KG isolation problem without
   LLM calls. Four-layer pipeline: FTS5 name matching → GLiNER NER
   (205M params, CPU) → co-occurrence edges → link prediction.
   Expected: 75% → ~21% unlinked.

2. **Quantum Integration** — deploy the 548-line quantum schema
   migration, integrate phase-aware amplitude scoring into the
   retrieval pipeline, implement collapse dynamics and belief
   superposition. Requires Pillar 1 (populated KG).

3. **Next-Gen Capabilities** — typed causal edges, temporal
   abstraction hierarchy, RL-trained memory operations,
   outcome-conditional forgetting.

---

## Pillar 1: Auto Entity Linking

### The Problem

Production coupling gate (v1.7.0+) shows:
- 178 active memories, 42 linked (24%), 136 isolated (76%)
- 4,862 knowledge_edges total but only 56 connect active memories
- 248 entities exist but most memories don't reference them
- Quantum interference algorithm returns P@5 parity (20%) because
  it has no edges to compute interference over

### The Solution: 4-Layer Zero-LLM Pipeline

**Layer 1 — FTS5 Entity Name Matching (zero dependencies)**

For each unlinked memory, check if any of the 248 entity names appear
as substrings. Use FTS5 trigram tokenizer for fuzzy matching. Build
alias table from entity names. Pure SQL.

Expected: 75% → ~49% unlinked.

**Layer 2 — GLiNER NER (205M params, CPU, no LLM)**

Zero-shot NER with labels matching brainctl entity types: person,
project, tool, service, concept, organization. Fuzzy-match extracted
entities against existing entities. Auto-create genuinely new ones.

Papers: Zaratiana et al. (2024) "GLiNER: Generalist Model for NER
using Bidirectional Transformer." NAACL 2024.

Expected: ~49% → ~27% unlinked.

**Layer 3 — Co-occurrence Edges (SPRIG pattern)**

For memories linked to 2+ entities, create entity-to-entity edges.
Optionally use GLiREL (Boylan et al., NAACL 2025) for typed relations.

Papers: Wang (2025) "Democratizing GraphRAG: Linear, CPU-Only Graph
Retrieval." arXiv:2602.23372.

Expected: Graph density increases 30-50%.

**Layer 4 — Link Prediction (PyKEEN)**

Train TransE/RotatE on the densified graph. Predict missing edges
above confidence threshold.

Expected: ~27% → ~21% unlinked.

### Additional Papers

- Min et al. (2025) "Towards Practical GraphRAG." CIKM 2025.
  SpaCy dependency parsing gets 94% of LLM KG quality.
- Gutierrez et al. (2024) HippoRAG. NeurIPS 2024. 48% of failures
  come from NER omissions — entity coverage is the #1 lever.
- Xu et al. (2025) A-MEM. arXiv:2502.12110. Zettelkasten-style
  keyword/tag extraction at write time.
- Otmazgin et al. (2022) F-COREF. AACL-IJCNLP. Fast coreference
  resolution as spaCy pipeline component.
- Cocchieri et al. (2025) ZeroNER. ACL Findings. Zero-shot NER via
  entity type descriptions.
- Shachar et al. (2025) NER Retriever. EMNLP Findings. Mid-layer
  transformer representations encode fine-grained entity types.

### Dependencies

- Layer 1: Zero (pure SQL)
- Layer 2: `pip install gliner` (~205M model)
- Layer 3: Zero (pure SQL)
- Layer 4: `pip install pykeen` (PyTorch)

---

## Pillar 2: Quantum Integration

### Current State

Wave 1 complete (6/6). Wave 2 analysis mostly complete (4/7 done):
- Phase inference: ✅ All 150 memories have phases computed
- Bell test: ✅ S=1.9995 for hermes↔agent-1 (classical bound)
- Hilbert dimension: ✅ Effective 159d @ 95% variance
- Empirical decoherence: ✅ All 4 predictions confirmed
- Collapse dynamics: 🔴 Design only, not implemented
- Quantum walk: ✅ Analysis done, 79% edge rot identified
- Schema migration: ⏳ 548 lines ready, not deployed

### v2.0 Quantum Deliverables

**Q1. Deploy quantum schema migration**

548-line atomic 8-phase migration at `research/quantum/
quantum_schema_migration_sqlite.sql`. Zero conflicts with existing
schema. Backward compatible. Adds: confidence_phase, hilbert_projection,
coherence_syndrome, decoherence_rate columns + belief_collapse_events,
agent_entanglement, agent_ghz_groups tables.

Blocker for all downstream quantum work.

**Q2. Integrate phase-aware amplitude scoring**

`quantum_amplitude_scorer_v2.py` exists (400 lines). Wire into the
RRF pipeline as a blended signal (50/50 classical+quantum default).
Requires populated KG from Pillar 1 to show improvement over parity.

**Q3. Implement collapse dynamics**

Four triggers: task_checkout, direct_query, evidence_threshold,
time_decoherence. Quantum Zeno effect: frequent measurement slows
collapse. Must preserve pre-collapse density matrix for auditability.

Design: `research/quantum/02_collapse_dynamics.md`

**Q4. Integrate decoherence into consolidation**

Replace brainctl's spacing-effect decay with power-law decoherence:
t^{-γ} where γ is adaptive per memory (based on contradictions,
citations, trust). Pointer states (high in-degree memories) resist
decoherence.

Synergy with v1.7.0 SHY: homeostatic pressure → decoherence rate
scaling.

### CLS ↔ Quantum Synergies

- Belief superposition = uncommitted hippocampal traces
- Collapse = CLS transfer (hippocampal → cortical commitment)
- Phase learning co-activation patterns = replay prioritization
- GHZ entanglement groups → schema-accelerated consolidation

---

## Pillar 3: Next-Gen Capabilities

### 3.1 Typed Causal Edges

**Paper:** Kang et al. (2025) "Hindsight: Causal attribution for
improved retrieval." arXiv:2512.12818.

Extend knowledge_edges with typed causal relations: `causes`, `enables`,
`prevents`, `preceded_by`. When a memory is retrieved and leads to a
task outcome, trace the causal chain backward and upweight contributing
memories. Counterfactual attribution: "would the outcome change if this
memory hadn't been retrieved?"

### 3.2 Temporal Abstraction Hierarchy

**Paper:** Shu et al. (2025) "TiMem: Temporal integration for
memory-augmented LLM agents." arXiv:2601.02845.

5-level memory tree: raw → session → day → week → month. Each level
is a progressively compressed summary. Achieves 52% memory length
reduction while maintaining retrieval quality. Maps to brainctl's
existing `temporal_level` column (moment/session/day/week/month/quarter)
but currently unused.

### 3.3 RL-Trained Memory Operations

**Paper:** Chen et al. (2025) "AgeMem: Agent memory with hybrid
strategy." arXiv:2601.01885.

Replace heuristic write gate (A-MAC) and consolidation triggers with
RL-trained policies. Actions: write, update, discard, merge, compress.
State: current memory stats, query patterns, task outcomes. Reward:
downstream task success. The agent learns WHEN to write, WHAT to
keep, and HOW to consolidate.

### 3.4 Outcome-Conditional Forgetting

**Paper:** Fountas et al. (2026) "Predictive forgetting for optimal
generalisation." arXiv:2603.04688.

Information-theoretic forgetting via I(X;Z|Y) minimization: forget
information X about memory Z that doesn't predict outcome Y.
Memories are retained not because they were important in the past
but because they predict future utility. Already partially
implemented via demand_forecast integration in v1.7.0 Tier B.

### 3.5 Federated Agent Memory

**Papers:**
- Yu et al. (2026) "Multi-agent memory from a computer architecture
  perspective." arXiv:2603.10062.
- Fleming et al. (2026) "Scaling multi-agent systems: Cognitive
  Fabric Nodes." arXiv:2604.03430.

Multiple agents with separate brain.db files sharing knowledge via
a synchronization protocol with cache coherence. Version counters,
conflict detection, semantic grounding before broadcast.

### 3.6 Knowledge Compounding Economics

**Paper:** Wen & Ku (2026) "Knowledge compounding." arXiv:2604.11243.

84.6% token savings vs. standard RAG when structured knowledge persists
across queries. Reconceptualizes LLM tokens from consumables to capital
goods — memory is an appreciating asset. Track compounding ROI per
project scope.

### Additional Frontier Papers

- Packer et al. (2023) MemGPT. OS-style memory management.
- Rasmussen et al. (2025) Zep/Graphiti. Bi-temporal knowledge graph.
- Chhikara et al. (2025) Mem0g. Production graph memory.
- Yang et al. (2026) Graph-based Agent Memory survey. arXiv:2602.05665.
- Du (2026) Memory for Autonomous LLM Agents survey. arXiv:2603.07670.
- Dong et al. (2026) Episodic memory for LLMs. Trends in Cognitive
  Sciences 30(2).

---

## Implementation Roadmap

### v2.0-alpha: Auto Entity Linking + Quantum Schema

| Task | Effort | Dependencies |
|------|--------|-------------|
| Layer 1: FTS5 entity name matching | Low | None |
| Layer 2: GLiNER NER integration | Medium | `pip install gliner` |
| Layer 3: Co-occurrence edges | Low | Layers 1-2 |
| Deploy quantum schema migration | Medium | Backup brain.db |
| Integrate phase-aware amplitude scorer | Medium | Quantum schema |

### v2.0-beta: Quantum Mechanics + Causal Edges

| Task | Effort | Dependencies |
|------|--------|-------------|
| Collapse dynamics implementation | High | Quantum schema |
| Decoherence → consolidation integration | Medium | Collapse dynamics |
| Typed causal edges | Medium | Entity linking |
| Temporal abstraction hierarchy | Medium | None |

### v2.0: Full Release

| Task | Effort | Dependencies |
|------|--------|-------------|
| Layer 4: Link prediction (PyKEEN) | Medium | Dense graph |
| RL-trained memory operations (AgeMem) | High | Q-value baseline |
| Outcome-conditional forgetting | Medium | Causal edges |
| Federated memory protocol | High | Multi-agent testing |
| Knowledge compounding metrics | Low | Usage tracking |

---

## Paper Count

- Pillar 1 (Entity Linking): 12 papers + 3 implementations
- Pillar 2 (Quantum): ~15 existing papers + analysis
- Pillar 3 (Frontier): 18 papers
- **Total new for v2.0: ~45 papers**
- **Grand total (v1.7–v2.0): ~120 papers**
