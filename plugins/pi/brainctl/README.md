# brainctl for Pi

Persistent memory for [Pi](https://github.com/badlogic/pi-mono) (Mario Zechner's
minimal terminal coding harness, npm `@mariozechner/pi-coding-agent`) powered
by [brainctl](https://pypi.org/project/brainctl/). All 201 brainctl MCP tools
are reachable from Pi through the community-standard
[`pi-mcp-adapter`](https://github.com/nicobailon/pi-mcp-adapter).

No long-running worker, no HTTP port, no LLM calls. One SQLite file.

## Why an adapter?

Pi is intentionally minimal. Mario [explicitly omitted MCP support](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md)
because inlining 200+ tool definitions at startup conflicts with Pi's
"shitty coding agent" / anti-bloat philosophy. The community escape hatch is
`pi-mcp-adapter`: a Pi extension that registers a **single `mcp` proxy tool**
(~200 tokens) and lazy-loads MCP servers on first call. So Pi keeps its
small startup surface while still reaching the full MCP ecosystem.

This plugin ships a 4-line `mcp.json` fragment plus a Python installer that
merges brainctl into the adapter's config without clobbering anything else.

## Install

```bash
# 1. install brainctl (pulls the brainctl-mcp console script)
pip install 'brainctl[mcp]>=2.4.2'

# 2. install pi-mcp-adapter (one-time, if you don't already have it)
pi install npm:pi-mcp-adapter

# 3. wire brainctl into the adapter's mcp.json
python3 plugins/pi/brainctl/install.py
```

Idempotent — rerunning is safe. Existing `mcpServers.*` entries from other
servers are preserved.

### One-shot install (adapter + brainctl wiring together)

```bash
python3 plugins/pi/brainctl/install.py --auto-install-adapter
```

### Dry-run first

```bash
python3 plugins/pi/brainctl/install.py --dry-run
```

### Uninstall (removes brainctl from mcp.json, leaves the adapter and other servers alone)

```bash
python3 plugins/pi/brainctl/install.py --uninstall
```

### Other flags

| Flag | Purpose |
|---|---|
| `--config <path>` | Explicit path to `mcp.json` (overrides `$PI_CODING_AGENT_DIR`) |
| `--force` | Overwrite an existing `mcpServers.brainctl` block that differs from the shipped fragment |
| `--no-validate` | Skip the `brainctl-mcp` PATH check |
| `--yes` | Reserved for non-TTY automation; no interactive prompts today |

## How the proxy exposes brainctl tools

Pi sees ONE tool named `mcp`. To invoke a brainctl tool through it:

```
mcp({ tool: "agent_orient", args: "{\"agent_id\": \"pi:myproj\", \"project\": \"myproj\"}" })
mcp({ tool: "memory_add",   args: "{\"content\": \"...\", \"category\": \"project\"}" })
```

If you set `directTools: true` (or list specific tools) in `mcp.json` settings,
the adapter promotes them to first-class tools with a configurable prefix
(`server`, `short`, or `none`). See the
[adapter README](https://github.com/nicobailon/pi-mcp-adapter) for the full
schema.

The bundled `AGENTS.md` documents this calling convention so your Pi sessions
have it in context automatically when copied into Pi's per-session context dir.

## Config locations

| Path | What it is |
|---|---|
| `~/.pi/agent/mcp.json` | adapter config (default) |
| `$PI_CODING_AGENT_DIR/mcp.json` | full base-dir override (replaces `~/.pi/agent`) |
| `--config <path>` | explicit per-invocation override |
| `~/agentmemory/db/brain.db` | the SQLite brain itself (override via `BRAINCTL_DB`) |

## Troubleshooting

**`pi-mcp-adapter not installed`**
The installer can't find the adapter. Either run `pi install npm:pi-mcp-adapter`
yourself, or rerun the installer with `--auto-install-adapter`.

**`brainctl-mcp not on PATH`**
The brainctl MCP server binary is missing. Run `pip install 'brainctl[mcp]>=2.4.2'`.
If you've installed brainctl into a venv, activate it before running the
installer (or pass `--no-validate` to bypass).

**`mcpServers.brainctl already exists ... differs`**
Someone (you, an earlier installer, an older version of this plugin) wrote a
brainctl block that doesn't match the shipped fragment. Pass `--force` to
overwrite, or open `~/.pi/agent/mcp.json` and reconcile by hand.

**Pi doesn't see brainctl tools after install**
Restart Pi. The adapter loads `mcp.json` at process start.

**Wanted directTools / per-tool routing**
Edit `~/.pi/agent/mcp.json` after install: add `"directTools": true` (or a list
of brainctl tool names) to the `brainctl` block. The shipped fragment is
deliberately minimal — adapter defaults are sane.

## Compatibility

- Pi (`@mariozechner/pi-coding-agent`) **>= 0.6**
- `pi-mcp-adapter` — any current version
- brainctl **>= 2.4.2**
- Python **>= 3.11**

## Roadmap

This is the v1 (MCP-via-adapter) track. A v2 native track — a Pi Package
(TypeScript extension) using `pi.on("tool_call")` to journal events without
the adapter dependency — may ship later if there's demand. The MCP track is
smaller, faster, and matches what Pi users already configure for other servers.

## License

MIT. Part of the [brainctl](https://github.com/TSchonleber/brainctl) project.
