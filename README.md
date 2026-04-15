# brainctl

**Your AI agent forgets everything between sessions. brainctl fixes that.**

One SQLite file gives your agent persistent memory — what it learned, who it talked to, what decisions were made, and why. No server. No API keys. No LLM calls.

```python
from agentmemory import Brain

brain = Brain(agent_id="my-agent")

# Start of session — get full context in one call
context = brain.orient(project="api-v2")
# → {'handoff': {...}, 'recent_events': [...], 'triggers': [...], 'memories': [...]}

# During work
brain.remember("API rate-limits at 100 req/15s", category="integration")
brain.decide("Use Retry-After for backoff", "Server controls timing", project="api-v2")
brain.entity("RateLimitAPI", "service", observations=["100 req/15s", "Retry-After header"])

# End of session — preserve state for next agent
brain.wrap_up("Documented rate limiting, auth module complete", project="api-v2")
```

Next session, a different agent (or the same one) picks up exactly where you left off.

## Install

```bash
pip install brainctl
```

That's it. No dependencies beyond Python 3.11+ and SQLite (built-in). Optional extras:

```bash
pip install brainctl[mcp]         # MCP server for Claude Desktop / VS Code
pip install brainctl[vec]         # vector similarity search (sqlite-vec + Ollama)
pip install brainctl[all]         # everything
```

## Upgrading

**Fresh installs**: nothing to do. `pip install brainctl` and your first `Brain()` call creates a `brain.db` with the full current schema.

**Upgrading an existing `brain.db`**: brainctl tracks schema migrations in a `schema_versions` table. After upgrading:

```bash
cp $BRAIN_DB $BRAIN_DB.pre-upgrade     # always back up first
brainctl doctor                         # diagnose migration state
brainctl migrate                        # apply anything pending
```

If `brainctl doctor` reports everything green, you're done.

### Predating the tracker — "virgin tracker + schema drift"

If your `brain.db` existed before the migration tracking framework was introduced, `schema_versions` will be empty but your schema already has the effects of many migrations. Running `brainctl migrate` blindly in that state will **crash** on the first `ALTER TABLE ADD COLUMN` that collides with an existing column — SQLite has no `IF NOT EXISTS` for column adds.

`brainctl doctor` detects this state and prints:

```
  migrations: virgin tracker + 5 ad-hoc schema hits — DANGEROUS to run `brainctl migrate` directly
    1. brainctl migrate --status-verbose   (see which migrations are truly pending)
    2. apply truly-pending ones manually via sqlite3
    3. brainctl migrate --mark-applied-up-to N (backfill the rest)
    4. brainctl migrate   (run anything above N)
```

**Full recovery workflow:**

```bash
# 1. Back up. Always.
cp $BRAIN_DB $BRAIN_DB.pre-migrate

# 2. Get a per-migration heuristic report.
#    Each migration is classified as:
#      likely-applied → all expected columns/tables exist
#      partial        → some DDL applied, some missing (actual drift)
#      pending        → none of its DDL exists (genuinely needs to run)
#      unknown        → no introspectable DDL (UPDATE-only or DROP-only)
brainctl migrate --status-verbose

# 3. For each migration in 'pending' or 'partial', apply it manually.
#    This is the safe path because you see exactly what each statement does.
sqlite3 $BRAIN_DB < db/migrations/024_confidence_alpha_beta_wiring.sql
sqlite3 $BRAIN_DB < db/migrations/028_memory_quarantine.sql
# ... etc

# 4. Backfill the tracker so future `brainctl migrate` runs skip
#    what's already applied. Pick N = highest version you've verified.
brainctl migrate --mark-applied-up-to 31

# 5. Run anything above N (e.g. migration 032 drops dead tables)
brainctl migrate
```

**`--mark-applied-up-to N`** writes rows to `schema_versions` with a `(backfilled)` suffix on the name so you can tell them apart from "really ran" rows. It refuses to go **below** the current high-water mark (guards against rewriting tracker state you've already committed to).

**Rollback**: if a migration run breaks something, `cp $BRAIN_DB.pre-migrate $BRAIN_DB` gets you back. `brain.db` is a single SQLite file — no out-of-band state to worry about.

## Quick Start

### Python API

```python
from agentmemory import Brain

brain = Brain()                    # creates ~/agentmemory/db/brain.db automatically

brain.remember("User prefers dark mode", category="preference")
brain.search("dark mode")          # FTS5 full-text search with stemming

brain.entity("Alice", "person", observations=["Engineer", "Likes Python"])
brain.relate("Alice", "works_at", "Acme")

brain.log("Deployed v2.0", event_type="result", project="myproject")
brain.decide("Keep JWT expiry at 24h", "Security vs UX balance")

brain.trigger("deploy fails", "deploy,failure,502", "Check rollback procedure")
brain.doctor()                     # {'healthy': True, 'active_memories': 5, ...}
```

### CLI

```bash
brainctl memory add "Auth uses JWT with 24h expiry" -c convention
brainctl search "auth"
brainctl entity create "Alice" -t person -o "Engineer"
brainctl entity relate Alice works_at Acme
brainctl entity compile Alice           # rebuild compiled_truth synthesis for an entity
brainctl entity get Alice --compiled    # return just the synthesis block
brainctl entity tier --refresh          # recompute T1/T2/T3 enrichment tiers
brainctl entity alias add Alice alicec  # canonical-name dedup hint
brainctl event add "Deployed v2.0" -t result -p myproject
brainctl trigger create "deploy issue" -k deploy,failure -a "Check rollback"
brainctl gaps scan                      # coverage + orphan-memory + broken-edge + unref-entity scans
brainctl stats
```

### MCP Server (Claude Desktop / VS Code / Cursor)

```json
{
  "mcpServers": {
    "brainctl": {
      "command": "brainctl-mcp"
    }
  }
}
```

199 tools available. See [MCP_SERVER.md](MCP_SERVER.md) for the full list and a decision tree showing which tools to use when.

## The Drop-In Pattern

Any agent, any framework. Three lines:

```python
context = brain.orient()           # session start: handoff + events + triggers + memories
# ... do work ...
brain.wrap_up("what I accomplished")  # session end: logs event + creates handoff
```

`orient()` returns a single dict with everything the agent needs: pending handoff from the last session, recent events, active triggers, relevant memories, and stats. `wrap_up()` creates a handoff packet so the next session can resume.

See [examples/](examples/) for runnable scripts and [docs/AGENT_ONBOARDING.md](docs/AGENT_ONBOARDING.md) for the full agent integration guide.

## Framework Integrations

### LangChain

```bash
pip install brainctl langchain-core
```

```python
from agentmemory.integrations.langchain import BrainctlChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

chain_with_history = RunnableWithMessageHistory(
    runnable=my_chain,
    get_session_history=lambda sid: BrainctlChatMessageHistory(session_id=sid),
)
```

Chat messages persist in brain.db. The Brain instance is accessible via `history.brain` for knowledge operations beyond chat (entities, decisions, triggers, search).

### CrewAI

```bash
pip install brainctl crewai
```

```python
from crewai import Crew
from crewai.memory import ShortTermMemory, LongTermMemory, EntityMemory
from agentmemory.integrations.crewai import BrainctlStorage

crew = Crew(
    agents=[...], tasks=[...], memory=True,
    short_term_memory=ShortTermMemory(storage=BrainctlStorage("short-term")),
    long_term_memory=LongTermMemory(storage=BrainctlStorage("long-term")),
    entity_memory=EntityMemory(storage=BrainctlStorage("entity")),
)
```

All crew memory goes to a single brain.db. FTS5 search out of the box, optional vector search with `pip install brainctl[vec]`.

### Agent harness plugins

First-party plugins that drop brainctl into agent-runner environments as persistent memory:

| Plugin | Target | What it does | Install |
|---|---|---|---|
| [`plugins/claude-code/brainctl/`](plugins/claude-code/brainctl/) | [Claude Code](https://claude.com/product/claude-code) | Hooks into `SessionStart` / `UserPromptSubmit` / `PostToolUse` / `SessionEnd` — orient on start, wrap_up on end, capture events during work | `python3 plugins/claude-code/brainctl/install.py` |
| [`plugins/codex/brainctl/`](plugins/codex/brainctl/) | [OpenAI Codex CLI](https://github.com/openai/codex) | Idempotent merge of `[mcp_servers.brainctl]` into `~/.codex/config.toml` + `AGENTS.md.template` for session bookends. Exposes the full 199-tool surface | `python3 plugins/codex/brainctl/install.py` |
| [`plugins/hermes/brainctl/`](plugins/hermes/brainctl/) | [Hermes Agent](https://hermes-agent.nousresearch.com) | Full `MemoryProvider` with auto-recall, auto-retain, `orient`/`wrap_up` bookends, and `MEMORY.md`/`USER.md` mirroring. Upstream bundling: [NousResearch/hermes-agent#9246](https://github.com/NousResearch/hermes-agent/pull/9246) | `hermes memory setup → brainctl` |
| [`plugins/eliza/brainctl/`](plugins/eliza/brainctl/) | [Eliza](https://github.com/elizaos/eliza) | TypeScript plugin (`@brainctl/eliza-plugin`) — spawns `brainctl-mcp` as a subprocess, exposes six actions (`BRAINCTL_REMEMBER` / `SEARCH` / `ORIENT` / `WRAP_UP` / `DECIDE` / `LOG`) plus an auto-recall memory provider | `npm install @brainctl/eliza-plugin` |

### Trading-strategy plugins

Strategy-mixin plugins that give algorithmic trading frameworks persistent memory across backtests and live runs:

| Plugin | Target | What it does |
|---|---|---|
| [`plugins/freqtrade/brainctl/`](plugins/freqtrade/brainctl/) | [Freqtrade](https://www.freqtrade.io) | `StrategyBrain` mixin — remembers indicator states, logs trade decisions, correlates backtest vs live outcomes |
| [`plugins/jesse/brainctl/`](plugins/jesse/brainctl/) | [Jesse](https://jesse.trade) | Same shape as the Freqtrade plugin, adapted to Jesse's strategy API |

## Python API (21 methods)

| Method | What it does |
|--------|-------------|
| `remember(content, category)` | Store a durable fact |
| `search(query)` | FTS5 full-text search with stemming |
| `vsearch(query)` | Vector similarity search (optional) |
| `forget(memory_id)` | Soft-delete a memory |
| `entity(name, type)` | Create or get an entity |
| `relate(from, rel, to)` | Link two entities |
| `log(summary, type)` | Log a timestamped event |
| `decide(title, rationale)` | Record a decision with reasoning |
| `trigger(condition, keywords, action)` | Set a future reminder |
| `check_triggers(query)` | Match triggers against text |
| `handoff(goal, state, loops, next)` | Save session state |
| `resume()` | Fetch + consume latest handoff |
| `orient(project)` | One-call session start |
| `wrap_up(summary)` | One-call session end |
| `doctor()` | Diagnostic health check |
| `consolidate()` | Promote important memories |
| `tier_stats()` | Write-tier distribution |
| `stats()` | Database overview |
| `affect(text)` | Classify emotional state |
| `affect_log(text)` | Classify + store emotional state |

## Core Concepts

**Memories** — Durable facts with categories that control their natural decay rate. Identity lasts a year; integration details fade in a month. Recalled memories get reinforced.

**Events** — Timestamped logs of what happened. Append-only. Searchable by type and project.

**Entities** — Typed nodes (person, project, tool, service) with observations. Form a self-building knowledge graph — when a memory mentions a known entity, the link is created automatically.

**Decisions** — Title + rationale. The "why" record. Prevents future agents from unknowingly contradicting prior choices.

**Triggers** — Prospective memory. "When X comes up, remind me to do Y." Fire on keyword match during search.

**Handoffs** — Working state packets for session continuity. Goal, current state, open loops, next step.

## What Makes It Different

| Feature | brainctl | mem0 | Zep | MemGPT |
|---------|----------|------|-----|--------|
| Single file (SQLite) | yes | - | - | - |
| No server required | yes | yes | - | - |
| No LLM calls | yes | - | yes | - |
| MCP server included | yes | - | - | - |
| Full-text search (FTS5) | yes | - | - | - |
| Vector search | yes | yes | yes | yes |
| Knowledge graph | yes | - | yes | - |
| Self-building graph | yes | - | - | - |
| Confidence decay | yes | - | - | - |
| Duplicate suppression | yes | - | - | - |
| Write gate (surprise scoring) | yes | - | - | - |
| Consolidation engine | yes | - | - | - |
| Prospective memory (triggers) | yes | - | - | - |
| Session handoffs | yes | - | - | - |
| Multi-agent support | yes | - | yes | - |
| Affect tracking | yes | - | - | - |
| Model-agnostic | yes | - | yes | - |

## Multi-Agent

Every operation accepts `agent_id` for attribution. Agents share one brain.db. Search sees everything. The knowledge graph connects insights across agents automatically.

```python
researcher = Brain(agent_id="researcher")
deployer = Brain(agent_id="deployer")

researcher.remember("Auth uses bcrypt cost=12", category="convention")
deployer.search("bcrypt")  # finds researcher's memory
```

## Context Profiles

Context profiles are task-scoped search presets. Instead of manually specifying `--tables` and `--category` on every query, name the task and brainctl loads only what's relevant.

```bash
brainctl search "voice" --profile writing     # memories: preference, convention, lesson
brainctl search "Sarah" --profile meeting     # contacts + interaction history + project context
brainctl search "JWT" --profile research      # technical knowledge + integrations
brainctl search "deploys" --profile ops       # events + decisions + project memories
brainctl search "founders" --profile networking  # entities (person, org) only
brainctl search "Q1" --profile review         # retrospective: lessons, decisions, projects
```

Works in MCP too:
```json
{ "tool": "memory_search", "query": "tone of voice", "profile": "writing" }
{ "tool": "search", "query": "auth system", "profile": "research" }
```

List all profiles, create your own, or delete custom ones:
```bash
brainctl profile list
brainctl profile show writing
brainctl profile create coderev \
  --categories convention,lesson \
  --tables memories,events \
  --description "Code review context"
brainctl profile delete coderev
```

Built-in profiles:

| Profile | Tables | Categories |
|---------|--------|------------|
| `writing` | memories, entities | preference, convention, lesson |
| `meeting` | memories, events, entities | user, project, preference |
| `research` | memories, entities | integration, convention, lesson, environment |
| `ops` | memories, events, decisions | project, decision, lesson |
| `networking` | entities, memories | user |
| `review` | memories, events, decisions | lesson, decision, project |

Profiles never override explicit `--tables` or `--category` flags — they're defaults, not locks.

## Obsidian Integration

Export brain.db to a navigable [Obsidian](https://obsidian.md) vault — and
optionally ingest new notes from the vault back into the brain through
the W(m) write gate. Karpathy's "LLM Wiki" pattern: brain.db is the
authoritative store, the markdown layer is the navigable overlay.

```bash
pip install brainctl[obsidian]

brainctl obsidian export ~/Documents/MyVault    # brain → markdown + wikilinks + frontmatter
brainctl obsidian import ~/Documents/MyVault    # ingest new vault notes through the W(m) gate
brainctl obsidian watch  ~/Documents/MyVault    # auto-ingest new notes on file changes
brainctl obsidian status ~/Documents/MyVault    # vault vs brain.db count delta
```

New markdown notes can carry `category: <one-of-the-documented-categories>`
in their frontmatter to control how they're filed; entity-shaped notes
under `brainctl/entities/` round-trip through `Brain.entity()` so they
become real entity rows, not memories. **One-way edits to already-exported
notes do not flow back to brain.db** — there's no merge / conflict logic
yet, so treat the export as canonical-from-brain and the vault layer as
edit-and-replay rather than two-way mirror.

## Memory Lifecycle

brainctl manages memories like biological memory:

- **Write gate** — Surprise scoring rejects redundant writes. Bypass with `force=True`.
- **Three-tier routing** — High-value memories get full indexing; low-value get lightweight storage.
- **Duplicate suppression** — Near-duplicates reinforce existing memories instead of creating new ones.
- **Half-life decay** — Unused memories fade based on category. Recalled memories get reinforced.
- **Hard cap** — 10,000 per agent. Emergency compression retires lowest-confidence memories.
- **Consolidation** — Batch maintenance: Hebbian learning, temporal promotion, compression. Schedule with cron.

## Health & Diagnostics

```python
brain.doctor()    # table checks, integrity, vec availability, DB size
```

```bash
brainctl stats    # database overview
brainctl lint     # quality issues (low confidence, duplicates, orphans)
brainctl lint --fix  # auto-fix safe issues
brainctl cost     # token usage dashboard
brainctl gaps scan   # coverage holes + orphan memories + broken edges + unreferenced entities
```

## Retrieval Quality Benchmark

brainctl ships a deterministic search-quality harness so changes to the hybrid FTS5+vec blend, intent classifier, or reranking profiles can be measured rather than guessed at.

```bash
python3 -m tests.bench.run                # JSON report: P@1 / P@5 / Recall@5 / MRR / nDCG@5
python3 -m tests.bench.run --check        # compare against committed baseline, fail on >2% regression
python3 -m tests.bench.run --update-baseline   # refresh the baseline after an intentional improvement
bin/brainctl-bench                        # first-class CLI wrapper (identical output)
```

Fixtures live under `tests/bench/fixtures.py`: 29 synthetic memories + 8 events + 6 entities + 20 graded queries across entity / procedural / decision / temporal / troubleshooting / negative / ambiguous classes. The regression gate runs in CI via `tests/test_search_quality_bench.py` and any >2% drop on a headline metric fails the build.

## Token Cost Optimization

```bash
brainctl search "deploy" --output oneline   # ~60 tokens (~97% savings vs JSON)
brainctl search "deploy" --budget 500       # hard token cap
brainctl search "deploy" --limit 3          # fewer results
```

## Vector Search (Optional)

Works without embeddings. For semantic similarity:

```bash
pip install brainctl[vec]
ollama pull nomic-embed-text       # install Ollama first: https://ollama.ai
brainctl embed populate            # backfill embeddings
brainctl vsearch "semantic query"
```

## Docker

```bash
docker build -t brainctl .
docker run -v ./data:/data brainctl                    # MCP server
docker run -v ./data:/data brainctl brainctl stats     # CLI
```

## Documentation

| Doc | What it covers |
|-----|---------------|
| [Agent Onboarding Guide](docs/AGENT_ONBOARDING.md) | Step-by-step integration for agents |
| [Agent Instructions](docs/AGENT_INSTRUCTIONS.md) | Copy-paste blocks for MCP, CLI, Python agents |
| [MCP Server Reference](MCP_SERVER.md) | 199 tools with decision tree |
| [Architecture](ARCHITECTURE.md) | Technical deep-dive |
| [Cognitive Protocol](COGNITIVE_PROTOCOL.md) | The Orient-Work-Record pattern |
| [Examples](examples/) | Runnable scripts (quickstart, lifecycle, multi-agent) |
| [Contributing](CONTRIBUTING.md) | Development setup and PR workflow |

## License

MIT
