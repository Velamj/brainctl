# brainctl plugin for Codex CLI

Give [OpenAI Codex CLI](https://github.com/openai/codex) persistent memory via [brainctl](https://github.com/TSchonleber/brainctl) — SQLite-backed long-term memory with FTS5 search, optional vector recall, a knowledge graph, and session handoffs. One file, zero servers, zero API keys.

> Your Codex agent forgets everything between sessions. This plugin fixes that.

## What it gives you

Codex discovers any MCP server listed in `~/.codex/config.toml` and exposes every tool it publishes to the model. This plugin wires `brainctl-mcp` in as a Codex MCP server, so the agent gets the full brainctl tool surface (196 tools) — remember, search, think, decide, log, entity ops, handoffs, affect tracking, and the rest.

Pair that with the included `AGENTS.md` template and your Codex sessions will:

- **Orient on start** — pull the last handoff packet, recent events, and task-relevant memories before doing anything
- **Accumulate memory while working** — durable facts via `brainctl_remember`, decisions via `brainctl_decide`, events via `brainctl_log`
- **Wrap up on end** — write a handoff packet with goal / current_state / open_loops / next_step so the next session can resume cleanly

## Prerequisites

```bash
pip install 'brainctl[mcp]>=1.3.0'
```

That puts `brainctl-mcp` on your PATH. Codex will spawn it as a subprocess whenever a session starts.

Optional: `pip install 'brainctl[vec]'` + `ollama pull nomic-embed-text` for vector recall.

## Install

From a cloned brainctl repo:

```bash
python3 plugins/codex/brainctl/install.py            # install
python3 plugins/codex/brainctl/install.py --dry      # preview only, no writes
python3 plugins/codex/brainctl/install.py --uninstall
```

The installer merges an idempotent `[mcp_servers.brainctl]` block into `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`). Existing MCP servers and other config are left untouched. Sentinel comments mark the brainctl block so uninstall is a clean removal.

If you'd rather paste the config yourself, run:

```bash
python3 plugins/codex/brainctl/install.py --print
```

and drop the output into your `config.toml`.

## Project instructions

The MCP server gives Codex the *ability* to use brainctl, but you still need to tell it *when*. Copy `AGENTS.md.template` into the root of any project where you want brainctl-backed memory:

```bash
cat plugins/codex/brainctl/AGENTS.md.template >> /path/to/your/project/AGENTS.md
```

Codex auto-loads `AGENTS.md` from the project root, so the brainctl lifecycle instructions become part of every session without any further configuration.

## Config

The MCP server block takes no config beyond the `BRAIN_DB` environment variable (optional — defaults to `~/agentmemory/db/brain.db`). Every other option lives in the brain itself via `brainctl config`.

| Env var | Default | Description |
|---------|---------|-------------|
| `BRAIN_DB` | `~/agentmemory/db/brain.db` | Path to the SQLite brain file |
| `BRAINCTL_AGENT_ID` | `codex` | Agent ID recorded on every write |

## What the installed config looks like

```toml
# >>> brainctl-mcp >>>
[mcp_servers.brainctl]
command = "brainctl-mcp"
args = []
startup_timeout_sec = 15
tool_timeout_sec = 60
env = { BRAINCTL_AGENT_ID = "codex" }
# <<< brainctl-mcp <<<
```

## Tool surface

Once installed, Codex gets 196 brainctl tools including:

| Tool | Purpose |
|------|---------|
| `memory_add` | Store a durable fact |
| `memory_search` | FTS5 full-text recall |
| `search` | Unified search across memories, events, entities |
| `think` | Spreading-activation recall across the knowledge graph |
| `decision_add` | Record a decision with rationale |
| `event_add` | Append to the event stream |
| `entity_create` / `entity_observe` / `entity_relate` | Knowledge-graph ops |
| `handoff_latest` / `handoff_add` | Session handoffs |
| `agent_orient` / `agent_wrap_up` | Native session bookends (new in v1.3.0) |
| `affect_log` / `affect_check` / `affect_classify` | Affect tracking |
| `pagerank` / `zoom_in` / `zoom_out` / `temporal_map` | Graph / temporal navigation |

See [`MCP_SERVER.md`](../../../MCP_SERVER.md) for the full list and decision tree.

## Troubleshooting

- **"command not found: brainctl-mcp"** — install with the `[mcp]` extra: `pip install 'brainctl[mcp]'`. A plain `pip install brainctl` doesn't include the MCP server entry point.
- **Codex doesn't see the tools** — run `codex --list-mcp-servers` (or check `~/.codex/config.toml`). Confirm the `[mcp_servers.brainctl]` block exists. Startup errors go to Codex's TUI log pane.
- **Slow startup** — brainctl's first run creates the SQLite schema. Subsequent starts are near-instant. If you see timeouts, bump `startup_timeout_sec` in the MCP block.
- **Wrong brain.db location** — set `BRAIN_DB` in the `env` map of the MCP block, or pass `--brain-db /path/to/brain.db` via `args`.

## License

MIT.
