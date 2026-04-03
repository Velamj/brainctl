# brainctl — Architecture

## Overview

brainctl is a persistent memory system for AI agents backed by a single SQLite
database. No server process, no external dependencies beyond Python and SQLite.
Multiple agents (or a single agent across sessions) share one `brain.db` file
for memories, events, entities, decisions, and a knowledge graph.

## Project Structure

```
src/agentmemory/
  __init__.py          Brain class export
  brain.py             Python API (Brain class)
  _impl.py             Full CLI implementation
  db.py                Shared DB utilities
  cli.py               Entry point
  mcp_server.py        MCP server entry
  commands/            23 command modules
    agent.py           Agent registration and state
    memory.py          Memory CRUD and search
    event.py           Event logging and queries
    entity.py          Entity management
    search.py          Cross-table search
    graph.py           Knowledge graph queries
    trigger.py         Prospective memory triggers
    trust.py           Trust scoring
    ...                (and more)

bin/
  brainctl             Thin CLI wrapper
  brainctl-mcp         MCP server launcher
  embed-populate       Embedding pipeline (optional)
  hippocampus.py       Consolidation engine

ui/
  server.py            Web dashboard (optional)
  static/              HTML/JS/CSS

db/
  init_schema.sql      Full schema definition
  migrations/          Incremental migrations
```

## Database Schema

All state lives in a single `brain.db` file (SQLite, WAL mode).

### Core Tables

| Table | Purpose |
|-------|---------|
| `memories` | Durable facts, preferences, lessons, conventions |
| `events` | Timestamped event log (append-oriented) |
| `entities` | Named entities (people, projects, tools, concepts) |
| `knowledge_edges` | Typed, weighted edges between any two records |
| `decisions` | Decisions with rationale |
| `memory_triggers` | Prospective memory (fire-when conditions) |
| `agents` | Registered agent identities |
| `agent_state` | Per-agent key/value store |
| `access_log` | Audit trail of all operations |
| `epochs` | Temporal segmentation for recency-weighted recall |
| `context` | Chunked knowledge (docs, conversations, code) |
| `tasks` | Shared task state |
| `embeddings` | Vector embedding storage |

See `db/init_schema.sql` for full column definitions and migrations.

### Vector Tables (optional, requires sqlite-vec)

| Table | Purpose |
|-------|---------|
| `vec_memories` | Memory embeddings (768-dim) |
| `vec_entities` | Entity embeddings |
| `vec_events` | Event embeddings |
| `vec_context` | Context chunk embeddings |

These tables are created only when sqlite-vec is installed. Everything else
works without them — you just lose vector search.

## Search Architecture

### FTS5 (always available)

Full-text search via SQLite FTS5 with porter + unicode61 tokenizers. Covers
memories, events, entities, and context. Accessed via `brainctl search`.

### Vector Search (optional)

Semantic search via sqlite-vec using cosine similarity (KNN). Requires:
- sqlite-vec Python package installed
- Ollama running locally with an embedding model (e.g., `nomic-embed-text`)
- Embeddings populated via `embed-populate`

Accessed via `brainctl vsearch`. Supports hybrid mode (FTS5 + vector,
alpha-weighted) or pure vector mode.

### Graph Traversal

Multi-hop neighbor queries across the knowledge graph via `brainctl graph`.

## Knowledge Graph

The `entities` table stores typed, named entities. The `knowledge_edges` table
connects any two records across any tables with typed, weighted edges:

```
source_table, source_id  -->  target_table, target_id
    relation_type (string)
    weight (0.0 - 1.0)
    agent_id (who created the edge)
```

Relation types include `topical_tag`, `topical_project`, `causal_chain_member`,
`causes`, `semantic_similar`, and any custom type agents define.

Edges are created by:
- The hippocampus consolidation cycle (topical edges)
- The events pipeline (causal edges)
- `embed-populate --graph-edges` (semantic similarity edges)
- Any agent manually via `brainctl graph add-edge`

## Consolidation Engine (hippocampus)

`bin/hippocampus.py` runs periodic maintenance on the memory store:

| Pass | What it does |
|------|-------------|
| **Decay** | Reduces access scores over time; memories that are never recalled fade |
| **Compression** | Merges clusters of related low-value memories into summaries |
| **Dream** | Synthesizes new hypotheses from loosely connected memories |
| **Hebbian** | Strengthens edges between frequently co-accessed records |

Run manually with `python bin/hippocampus.py` or schedule it via cron.

## Concurrency Model

- **WAL mode**: Multiple concurrent readers, single writer at a time
- **Timeout 10s**: Write attempts queue rather than fail immediately
- **No server process**: Zero overhead, no port conflicts, no daemon to manage
- **File locking**: Handled by SQLite's built-in locking (works on all platforms)

## Modular Command Structure

The CLI is organized into 23 command modules under `src/agentmemory/commands/`.
Each module handles one domain (memory, event, entity, graph, trigger, etc.)
and registers its subcommands with the main CLI parser. This keeps the codebase
navigable and makes it easy to add new command groups.

## Requirements

**Required:**
- Python 3.11+
- SQLite 3.35+ (for generated columns; most systems ship this)

**Optional:**
- `sqlite-vec` — enables vector search tables. Everything works without it.
- Ollama with an embedding model — needed only for `embed-populate` and `vsearch`.

## Backup and Maintenance

```bash
brainctl backup              # On-demand database backup
brainctl validate            # Integrity check
brainctl prune-log --days 30 # Trim old access log entries
brainctl health              # SLO dashboard
```
