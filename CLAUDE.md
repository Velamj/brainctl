# Claude Code — brainctl / agentmemory

## What This Is
Unified agent memory system. SQLite-backed (brain.db) with FTS5, vector embeddings (sqlite-vec + Ollama nomic-embed-text), knowledge graph, affect tracking, belief collapse mechanics, and AGM conflict resolution.

Published as `brainctl` on PyPI (v2.2.1+).

## Key Paths
- **DB:** `db/brain.db` (WAL mode, foreign keys ON, 61 user-facing tables, 47 migrations)
- **CLI:** `bin/brainctl` — main CLI entry
- **MCP server:** canonical entry is `agentmemory.mcp_server:run` (199 tools across `mcp_server.py` + 28 `mcp_tools_*.py` modules). Installed as the `brainctl-mcp` console script via pip. The legacy standalone `bin/brainctl-mcp` only registers a subset and is being phased out.
- **Bench:** `bin/brainctl-bench` — retrieval eval harness (P@k / MRR / nDCG@k regression gate, fixtures under `tests/bench/`)
- **Source:** `src/agentmemory/` — Python package
- **Config:** `config/` — quiet hours, consolidation schedules
- **Agents:** `agents/` — per-agent config (pipeline, engram, etc.)

## Build & Test
```bash
pip install -e .                                      # dev install
brainctl stats                                        # verify DB
brainctl search "test"                                # test search
python3 -m agentmemory.mcp_server --list-tools        # full 199-tool MCP surface
python3 -m tests.bench.run                            # retrieval quality benchmark
python3 -m tests.bench.run --check                    # fail on >2% regression vs baseline
```

## Architecture
- Tables: memories, events, entities, decisions, context, knowledge_edges, affect_log, access_log, agent_state, agent_beliefs
- FTS5 indexes on memories, events, entities
- Vector embeddings via sqlite-vec extension
- Hybrid retrieval: FTS5 + vector via Reciprocal Rank Fusion, routed through a regex intent classifier (`bin/intent_classifier.py`) that normalises 10 intent labels onto 6 rerank profiles inside `cmd_search`
- Retrieval regression-gated by `tests/bench/` (P@1 / P@5 / Recall@5 / MRR / nDCG@5; >2% drop fails CI)
- W(m) worthiness gate on memory writes (surprise scoring + semantic dedup)
- PII recency gate (Proactive Interference Index) on supersedes
- Bayesian alpha/beta tracking on memory recall
- Entities carry a rewriteable `compiled_truth` synthesis, a 3-level `enrichment_tier`, and a first-class `aliases` JSON list (migrations 033–035)
- Knowledge-gap scanner (`brainctl gaps scan`) also detects orphan memories, broken knowledge_edges, and unreferenced entities (migration 036)

## Conventions
- Agent IDs: use descriptive names like `my-agent`, `research-bot`, `code-reviewer`
- Memory categories: convention, decision, environment, identity, integration, lesson, preference, project, user
- Event types: artifact, decision, error, handoff, memory_promoted, memory_retired, observation, result, session_start, session_end, stale_context, task_update, warning
- Entity types: agent, concept, document, event, location, organization, other, person, project, service, tool

## Don't Touch
- Migration files in `db/migrations/` — append-only
- The W(m) gate logic without understanding surprise scoring
- Quiet hours scripts — they're cron-scheduled
