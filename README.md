# brainctl

**Forgetful agents, fixed by a SQLite file.**

One `brain.db` gives your agent durable memory across sessions — facts learned, decisions made, entities tracked, and state handed off. No server. No API keys. No LLM calls required.

```python
from agentmemory import Brain

brain = Brain(agent_id="my-agent")
ctx = brain.orient(project="api-v2")           # session start: handoff + events + triggers + memories
brain.remember("rate-limit: 100/15s", category="integration")
brain.decide("use Retry-After for backoff", "server controls timing", project="api-v2")
brain.wrap_up("auth module complete", project="api-v2")  # session end: logs + handoff for next run
```

## Install

```bash
pip install brainctl
```

Requires Python 3.11+. SQLite is built-in. No other mandatory dependencies.

```bash
pip install brainctl[mcp]     # MCP server — 201 tools for Claude Desktop, Cursor, VS Code
pip install brainctl[vec]     # vector similarity search (sqlite-vec + Ollama)
pip install brainctl[signing] # Ed25519-signed memory exports + optional Solana on-chain pinning
pip install brainctl[all]     # everything
```

## 5-line example

```python
from agentmemory import Brain

brain = Brain(agent_id="research-bot")
brain.remember("OpenAI rate-limits at 500k TPM on tier 3", category="integration")
results = brain.search("rate limit")          # FTS5 full-text, stemming, ranked
brain.entity("OpenAI", "service", observations=["500k TPM tier 3", "REST API"])
brain.relate("OpenAI", "provides", "GPT-4o")
```

## Feature checklist

**Memory types**
- `convention`, `decision`, `environment`, `identity`, `integration`, `lesson`, `preference`, `project`, `user`
- Category controls natural half-life: identity decays over ~1 year; integration details over ~1 month
- Hard cap: 10,000 memories per agent. Emergency compression retires lowest-confidence entries.

**Retrieval modes**
- FTS5 full-text search with stemming (default, zero dependencies)
- Vector similarity via sqlite-vec + Ollama nomic-embed-text (`brainctl[vec]`)
- Hybrid: Reciprocal Rank Fusion over FTS5 + vector results
- Context profiles: named search presets scoped to task type (`--profile ops`, `--profile research`, etc.)
- `--benchmark` preset: flattens recency/salience for synthetic evaluation runs

**Reranker chain**
- Intent classifier (regex, 10 labels → 6 profiles) routes queries at `cmd_search`
- Post-FTS reranking by recency, salience, Q-value utility, and Bayesian recall confidence
- Cold-start: auto-detects available reranker backends (cross-encoder > sentence-transformers > fallback)
- Retrieval regression-gated in CI: >2% drop on P@1/P@5/MRR/nDCG@5 fails the build

**Knowledge graph**
- Typed entity nodes: `agent`, `concept`, `document`, `event`, `location`, `organization`, `person`, `project`, `service`, `tool`
- Auto entity linking: memories mentioning a known entity create the edge automatically
- Compiled truth synthesis per entity (`brainctl entity compile <name>`)
- 3-level enrichment tier; canonical alias dedup (`brainctl entity alias add`)
- Spreading-activation recall across the graph (`brain.think(query)`)

**Belief revision (AGM)**
- Belief set per agent with confidence weights
- Conflict detection and resolution via `brainctl belief conflicts` and `brainctl belief merge`
- Collapse mechanics: decoherent beliefs quarantined, recovery candidates surfaced
- PII recency gate (Proactive Interference Index) on supersedes operations

**Signed exports**
- `brainctl export --sign` produces a portable Ed25519-signed JSON bundle
- `brainctl verify <bundle.json>` checks the signature offline — no brainctl required for verification
- Optional: `--pin-onchain` writes the SHA-256 hash as a Solana memo transaction (~$0.001 per pin)
- Managed wallet: `brainctl wallet new` creates a local keypair at `~/.brainctl/wallet.json` for users without an existing Solana setup
- Memories never leave the machine; only the hash goes on-chain (opt-in)

**Plugins (16 first-party)**

Agent frameworks:

| Plugin | Target |
|--------|--------|
| `plugins/claude-code/` | Claude Code |
| `plugins/codex/` | OpenAI Codex CLI |
| `plugins/cursor/` | Cursor |
| `plugins/gemini-cli/` | Gemini CLI |
| `plugins/eliza/` | Eliza (TypeScript) |
| `plugins/hermes/` | Hermes Agent |
| `plugins/openclaw/` | OpenClaw |
| `plugins/rig/` | Rig |
| `plugins/virtuals-game/` | Virtuals Game |
| `plugins/zerebro/` | Zerebro |

Trading bots:

| Plugin | Target |
|--------|--------|
| `plugins/freqtrade/` | Freqtrade |
| `plugins/jesse/` | Jesse |
| `plugins/hummingbird/` | Hummingbird |
| `plugins/nautilustrader/` | NautilusTrader |
| `plugins/octobot/` | OctoBot |
| `plugins/coinbase-agentkit/` | Coinbase AgentKit |

## MCP server (201 tools)

```json
{
  "mcpServers": {
    "brainctl": {
      "command": "brainctl-mcp"
    }
  }
}
```

Add to `~/.claude/claude_desktop_config.json`, `~/.cursor/mcp.json`, or equivalent. Full tool list and a decision tree: [MCP_SERVER.md](MCP_SERVER.md).

## CLI reference

```bash
brainctl memory add "content" -c convention   # store a memory
brainctl search "query"                       # FTS5 search
brainctl vsearch "semantic query"             # vector search (requires [vec])
brainctl entity create "Alice" -t person      # create entity
brainctl entity relate Alice works_at Acme    # link entities
brainctl event add "deployed v3" -t result    # log an event
brainctl decide "title" -r "rationale"        # record a decision
brainctl export --sign -o bundle.json         # signed export
brainctl verify bundle.json                   # verify a bundle
brainctl wallet new                           # create managed signing wallet
brainctl stats                                # DB overview
brainctl doctor                               # health check
brainctl lint                                 # quality issues
brainctl gaps scan                            # coverage + orphan + broken-edge scans
brainctl consolidate cycle                    # full consolidation pass
```

## Python API (22 methods)

| Method | What it does |
|--------|--------------|
| `orient(project)` | One-call session start: handoff + events + triggers + memories |
| `wrap_up(summary)` | One-call session end: logs event + creates handoff |
| `remember(content, category)` | Store a durable fact through the W(m) write gate |
| `search(query)` | FTS5 full-text search with stemming |
| `vsearch(query)` | Vector similarity search (optional) |
| `think(query)` | Spreading-activation recall across the knowledge graph |
| `forget(memory_id)` | Soft-delete a memory |
| `entity(name, type)` | Create or retrieve an entity |
| `relate(from, rel, to)` | Link two entities |
| `log(summary, type)` | Log a timestamped event |
| `decide(title, rationale)` | Record a decision with reasoning |
| `trigger(condition, keywords, action)` | Set a prospective reminder |
| `check_triggers(query)` | Match triggers against text |
| `handoff(goal, state, loops, next)` | Save session state explicitly |
| `resume()` | Fetch and consume latest handoff |
| `doctor()` | Diagnostic health check |
| `consolidate()` | Promote high-importance memories |
| `tier_stats()` | Write-tier distribution |
| `stats()` | Database overview |
| `affect(text)` | Classify emotional state |
| `affect_log(text)` | Classify and store emotional state |
| `close()` | Close the shared SQLite connection |

## Memory lifecycle

- **Write gate** (W(m)): surprise scoring rejects redundant writes. Bypass with `force=True`.
- **Three-tier routing**: high-value memories get full indexing; low-value get lightweight storage.
- **Duplicate suppression**: near-duplicates reinforce existing memories instead of creating new rows.
- **Half-life decay**: unused memories fade at a rate set by category. Recalled memories are reinforced.
- **Consolidation**: Hebbian learning, temporal promotion, compression — runs on a cron schedule.

## Retrieval benchmarks

Tested with default settings, no tuning for benchmark data. Backend: `Brain.search`.

**LongMemEval** (289 questions, 4 categories):

| metric | overall | single-session-assistant | single-session-user | multi-session |
|--------|---------|--------------------------|---------------------|---------------|
| hit@1  | 0.882 | 1.000 | 0.900 | 0.910 |
| hit@5  | 0.976 | 1.000 | 1.000 | 0.985 |
| MRR    | 0.924 | 1.000 | 0.935 | 0.944 |

**LOCOMO** (1,982 questions, 5 categories, 10 conversations):

| metric | overall | adversarial | temporal | open-domain | single-hop | multi-hop |
|--------|---------|-------------|----------|-------------|------------|-----------|
| hit@1  | 0.341 | 0.377 | 0.405 | 0.373 | 0.167 | 0.174 |
| hit@5  | 0.572 | 0.603 | 0.648 | 0.602 | 0.429 | 0.315 |
| MRR    | 0.445 | 0.479 | 0.510 | 0.479 | 0.282 | 0.232 |

LOCOMO single-hop and multi-hop hit@1 are weak (0.167 / 0.174). Root cause: recency and salience rerankers bias toward recent memories; LOCOMO uses uniform synthetic timestamps with gold evidence concentrated in early sessions, so the rerankers fight FTS ranking. A `--benchmark` preset that flattens recency/salience is available for evaluation runs. See `tests/bench/` for the full harness.

## Upgrading

```bash
cp $BRAIN_DB $BRAIN_DB.pre-upgrade
brainctl doctor      # diagnose migration state
brainctl migrate     # apply pending migrations
```

For databases predating the migration tracker, see the full recovery workflow in the README's Upgrading section (below the install block in the full docs).

## Multi-agent

```python
researcher = Brain(agent_id="researcher")
writer     = Brain(agent_id="writer")

researcher.remember("API uses OAuth 2.0 PKCE", category="integration")
writer.search("OAuth")   # finds researcher's memory — same brain.db, shared graph
```

Every operation accepts `agent_id` for attribution. Agents share one `brain.db`. The knowledge graph connects insights across agents automatically.

## Documentation

| Doc | What it covers |
|-----|---------------|
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | 60-second onboarding — install, remember, search, sign |
| [docs/COMPARISON.md](docs/COMPARISON.md) | Feature matrix vs Mem0, Letta, Zep, Cognee, OpenAI Memory |
| [docs/AGENT_ONBOARDING.md](docs/AGENT_ONBOARDING.md) | Step-by-step agent integration guide |
| [docs/AGENT_INSTRUCTIONS.md](docs/AGENT_INSTRUCTIONS.md) | Copy-paste blocks for MCP, CLI, Python agents |
| [docs/SIGNED_EXPORTS.md](docs/SIGNED_EXPORTS.md) | Bundle format, threat model, verify-without-brainctl recipe |
| [MCP_SERVER.md](MCP_SERVER.md) | 201 tools with decision tree |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Technical deep-dive |

## License

MIT
