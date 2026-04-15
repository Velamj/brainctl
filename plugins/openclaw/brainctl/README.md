# brainctl plugin for OpenClaw

Give [OpenClaw](https://openclaw.ai)'s Pi agent persistent memory via [brainctl](https://github.com/TSchonleber/brainctl) — SQLite-backed long-term memory with FTS5 search, optional vector recall, a knowledge graph, and session handoffs.

> Pi forgets everything between sessions. This plugin fixes that.

## Integration shape

OpenClaw auto-injects three prompt files from the workspace root into every Pi session — `AGENTS.md`, `SOUL.md`, and `TOOLS.md` — and loads skills from `<workspace>/skills/<skill-name>/SKILL.md`. This plugin uses that surface:

- It drops a `brainctl` skill into `<workspace>/skills/brainctl/SKILL.md` so Pi has a detailed reference for the CLI on hand.
- It merges a short sentinel-wrapped "Persistent memory" section into `<workspace>/AGENTS.md` so every session starts knowing memory exists and where to look for usage details.

This plugin deliberately does **not** install an MCP server and **does not touch** the root `~/.openclaw/openclaw.json`, nor does it register anything with OpenClaw's npm plugin system. Pi invokes brainctl by shelling out to the plain `brainctl` CLI in its shell.

If and when OpenClaw ships first-class MCP support, this plugin will grow an MCP install path alongside the skill path — the same shape already used by `plugins/cursor/` and `plugins/codex/`.

## Prerequisites

```bash
pip install 'brainctl>=1.2.0'
```

That puts the `brainctl` CLI on PATH for whatever shell OpenClaw spawns Pi in.

Optional: `pip install 'brainctl[vec]'` + `ollama pull nomic-embed-text` for vector recall.

## Install

From a cloned brainctl repo:

```bash
python3 plugins/openclaw/brainctl/install.py                    # install
python3 plugins/openclaw/brainctl/install.py --dry              # preview only
python3 plugins/openclaw/brainctl/install.py --uninstall        # remove
python3 plugins/openclaw/brainctl/install.py --path /custom/workspace
```

The default workspace is `~/.openclaw/workspace` (or `$OPENCLAW_HOME/workspace` if set).

## What the installer does

- Copies `SKILL.md.template` to `<workspace>/skills/brainctl/SKILL.md`, creating parent directories as needed.
- Merges `AGENTS.md.snippet` into `<workspace>/AGENTS.md` wrapped in HTML-comment sentinels (`<!-- >>> brainctl >>> -->` / `<!-- <<< brainctl <<< -->`) so future runs update the block in place.
- Backs up any pre-existing `AGENTS.md` to `AGENTS.md.brainctl.bak` before overwriting.
- Idempotent: rerunning with no changes produces no writes and prints `already up to date`.
- Leaves every other file in your OpenClaw workspace untouched.

## Config

| Env var | Default | Description |
|---------|---------|-------------|
| `BRAIN_DB` | `~/agentmemory/db/brain.db` | Path to the SQLite brain file |
| `BRAINCTL_AGENT_ID` | `openclaw` | Agent ID recorded on every write |
| `OPENCLAW_HOME` | `~/.openclaw` | OpenClaw root — workspace is `$OPENCLAW_HOME/workspace` |

All other brainctl options live inside the brain itself via `brainctl config`.

## Troubleshooting

- **Pi doesn't reach for brainctl** — open `<workspace>/AGENTS.md` and confirm it still contains the `<!-- >>> brainctl >>> -->` block. If it's missing, rerun the installer. OpenClaw only injects what's in `AGENTS.md`; if the block is gone, Pi has no reason to know memory exists.
- **`brainctl: command not found` in Pi's shell** — install brainctl into the Python environment OpenClaw's shell inherits: `pip install 'brainctl>=1.2.0'`. The skill file assumes `brainctl` is on PATH for every shell Pi spawns.
- **Pi writes to memory but never reads from it** — that usually means the AGENTS.md snippet was stripped or never installed. The snippet is what tells Pi *when* to call `orient`; without it, Pi treats brainctl as write-only. Rerun the installer.
- **Wrong `brain.db` location** — set `BRAIN_DB` in the environment OpenClaw spawns Pi in. brainctl defaults to `~/agentmemory/db/brain.db`.

## License

MIT. See the main [brainctl](https://github.com/TSchonleber/brainctl) repository for the full license text.
