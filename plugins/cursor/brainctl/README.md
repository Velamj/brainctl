# brainctl plugin for Cursor

Give [Cursor](https://cursor.com) persistent memory via [brainctl](https://github.com/TSchonleber/brainctl) — SQLite-backed long-term memory with FTS5 search, optional vector recall, a knowledge graph, and session handoffs. One file, zero servers, zero API keys.

> Your Cursor agent forgets everything between chats. This plugin fixes that.

## What it gives you

Cursor discovers any MCP server listed in `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project-local) and exposes every tool it publishes to the agent. This plugin wires `brainctl-mcp` in as a Cursor MCP server, so the agent gets the full brainctl tool surface (199 tools) — remember, search, think, decide, log, entity ops, handoffs, affect tracking, and the rest.

Pair that with the included `.cursor/rules/brainctl.mdc` template and your Cursor sessions will:

- **Orient on start** — pull the last handoff packet, recent events, and task-relevant memories before doing anything
- **Accumulate memory while working** — durable facts via `memory_add`, decisions via `decision_add`, events via `event_add`
- **Wrap up on end** — write a handoff packet with goal / current_state / open_loops / next_step so the next session can resume cleanly

## Prerequisites

```bash
pip install 'brainctl[mcp]>=1.3.0'
```

That puts `brainctl-mcp` on your PATH. Cursor will spawn it as a subprocess whenever a session starts.

Optional: `pip install 'brainctl[vec]'` + `ollama pull nomic-embed-text` for vector recall.

## Install

From a cloned brainctl repo:

```bash
python3 plugins/cursor/brainctl/install.py              # install (global, ~/.cursor/mcp.json)
python3 plugins/cursor/brainctl/install.py --project    # install into ./.cursor/mcp.json
python3 plugins/cursor/brainctl/install.py --dry        # preview only, no writes
python3 plugins/cursor/brainctl/install.py --uninstall
```

The installer merges an idempotent `brainctl` entry into the top-level `mcpServers` object of `~/.cursor/mcp.json` (or `$CURSOR_HOME/mcp.json`, or a project-local `./.cursor/mcp.json` with `--project`). Existing MCP servers and other top-level keys are left untouched. A `.brainctl.bak` backup is written before any edit so uninstall is a clean removal.

If you'd rather paste the config yourself, run:

```bash
python3 plugins/cursor/brainctl/install.py --print
```

and drop the output into your `mcp.json`.

## Project instructions

The MCP server gives Cursor the *ability* to use brainctl, but you still need to tell it *when*. Copy `rules.mdc.template` into `.cursor/rules/brainctl.mdc` at the root of any project where you want brainctl-backed memory:

```bash
mkdir -p /path/to/your/project/.cursor/rules
cp plugins/cursor/brainctl/rules.mdc.template /path/to/your/project/.cursor/rules/brainctl.mdc
```

Cursor auto-loads `.cursor/rules/*.mdc` files with `alwaysApply: true` on every session, so the brainctl lifecycle instructions become part of every chat without any further configuration.

## Config

The MCP server entry takes no config beyond the `BRAIN_DB` environment variable (optional — defaults to `~/agentmemory/db/brain.db`). Every other option lives in the brain itself via `brainctl config`.

| Env var | Default | Description |
|---------|---------|-------------|
| `BRAIN_DB` | `~/agentmemory/db/brain.db` | Path to the SQLite brain file |
| `BRAINCTL_AGENT_ID` | `cursor` | Agent ID recorded on every write |
| `CURSOR_HOME` | `~/.cursor` | Override directory used to locate `mcp.json` |

## What the installed config looks like

```json
{
  "mcpServers": {
    "brainctl": {
      "command": "brainctl-mcp",
      "args": [],
      "env": {
        "BRAINCTL_AGENT_ID": "cursor"
      }
    }
  }
}
```

## Tool surface

Once installed, Cursor gets 196 brainctl tools including:

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
- **Cursor doesn't see the tools** — open Cursor Settings → MCP and check the `brainctl` row. If it's red, click through to inspect the stderr log for the subprocess; most failures are a missing `brainctl-mcp` on PATH or a bad `BRAIN_DB` path.
- **Tools listed but the agent ignores them** — make sure `.cursor/rules/brainctl.mdc` exists at the project root and still has `alwaysApply: true` in its frontmatter. Without the rules file Cursor has the tools but no instructions on when to call them.
- **Wrong brain.db location** — set `BRAIN_DB` in the `env` map of the MCP entry, or export it in the shell you launched Cursor from.

## License

MIT. See the main [brainctl](https://github.com/TSchonleber/brainctl) repo.
