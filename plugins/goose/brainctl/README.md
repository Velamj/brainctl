# brainctl for Goose

Persistent agent memory for [Goose](https://github.com/aaif-goose/goose)
(Block / now AAIF / Linux Foundation), powered by
[brainctl](https://pypi.org/project/brainctl/) — 201 MCP tools,
SQLite-backed (`brain.db`), local-first, MIT-licensed.

Goose is **MCP-only** (no hook surface like Claude Code or OpenCode), so
this plugin is just a YAML fragment that registers brainctl as an `stdio`
MCP extension under `extensions.brainctl`. No long-running worker, no
HTTP port, no LLM calls.

## What you get

All 201 brainctl MCP tools available to Goose under the `brainctl__*`
prefix. Highlights:

- `brainctl__agent_orient`, `brainctl__agent_wrap_up`,
  `brainctl__handoff_add` — session continuity
- `brainctl__memory_add`, `brainctl__memory_search`, `brainctl__vsearch`
  — durable facts + retrieval
- `brainctl__decision_add` — log non-trivial choices with rationale
- `brainctl__entity_create`, `brainctl__entity_observe`,
  `brainctl__entity_relate` — knowledge graph
- `brainctl__event_add` — timestamped session journal
- `brainctl__infer`, `brainctl__reason`, `brainctl__think` — built-in
  reflexion / reasoning loops

See `GOOSE.md` (in this directory) for the at-a-glance guide you can paste
into your Goose profile.

## Install

```bash
pip install 'brainctl[mcp]>=2.4.2'
python3 plugins/goose/brainctl/install.py
```

The installer merges the brainctl extension into Goose's YAML config and
is idempotent — rerunning is safe.

Dry-run first if you're nervous:

```bash
python3 plugins/goose/brainctl/install.py --dry-run
```

Override the config path (otherwise auto-detected per OS):

```bash
python3 plugins/goose/brainctl/install.py --config /path/to/config.yaml
# or
GOOSE_CONFIG_PATH=/path/to/config.yaml python3 plugins/goose/brainctl/install.py
```

Uninstall (leaves any other extensions intact):

```bash
python3 plugins/goose/brainctl/install.py --uninstall
```

## Installer flag matrix

| Flag | Purpose |
|---|---|
| `--config <path>` | Override Goose config path. |
| `--dry-run`, `--dry` | Print the resulting YAML to stdout without writing. |
| `--uninstall` | Remove `extensions.brainctl`, keep all other extensions. |
| `--force` | Overwrite an existing `extensions.brainctl` block. |
| `--no-validate` | Skip the `brainctl-mcp` PATH check. |
| `--yes` | Non-TTY safe; this installer never prompts so it's a no-op today. |

Default config locations:

| OS | Path |
|---|---|
| Linux / macOS | `~/.config/goose/config.yaml` |
| Windows | `%APPDATA%\Block\goose\config\config.yaml` |

The `GOOSE_CONFIG_PATH` env var overrides the default; `--config` overrides
both.

## Config customization

Environment variables on the brainctl-mcp process (set by the manifest):

| Variable | Default | Purpose |
|---|---|---|
| `BRAINCTL_DB` | `${HOME}/agentmemory/db/brain.db` | SQLite brain path. Edit `envs.BRAINCTL_DB` in your Goose config to point elsewhere. |

The merged extension block looks like:

```yaml
extensions:
  brainctl:
    bundled: false
    enabled: true
    name: "brainctl"
    type: "stdio"
    timeout: 300
    cmd: "brainctl-mcp"
    args: []
    description: "brainctl agent memory — 201 MCP tools, local-first SQLite, MIT-licensed"
    env_keys: []
    envs:
      BRAINCTL_DB: "${HOME}/agentmemory/db/brain.db"
    available_tools: []
```

To customize the DB path, edit the `envs.BRAINCTL_DB` value in your Goose
config after install (or rerun with `--force` after editing the
`BRAINCTL_BLOCK` constant in `install.py`).

To restrict which brainctl tools Goose loads, add tool names to
`available_tools`; an empty list (the default) means **all tools**.

## Troubleshooting

### `brainctl-mcp` not on PATH

Install error before any write happens. Fix:

```bash
pip install brainctl[mcp]
which brainctl-mcp     # should print a path
```

Skip the check with `--no-validate` if you're installing brainctl in a
venv that Goose will activate later.

### YAML parse errors

The installer reads your existing config defensively. If it can't parse,
you'll see:

```
[brainctl] ERROR: failed to parse <path>: <yaml error>
```

The fix is to repair the YAML manually (your config is untouched until
parsing succeeds). The installer will not silently mangle a broken file.

### pyyaml missing on a non-empty config

To merge into a config that already has an `extensions:` block, the
installer needs `pyyaml` so it can round-trip your other extensions
faithfully. Install it once:

```bash
pip install pyyaml
```

(Empty / brand-new configs work without pyyaml — the fallback emitter
handles those.)

### `extensions.brainctl exists — pass --force to overwrite`

Idempotency safety: a previous install already wrote the brainctl block
and the current contents differ from what we'd write. Either:

- Confirm the diff and rerun with `--force` to overwrite, or
- `--uninstall` first, then reinstall.

If the existing block is byte-identical to the default, the installer
exits 0 with `brainctl extension already up to date` and no `--force` is
needed.

### Conflicting extensions block

If `extensions:` exists in your config but isn't a YAML mapping (e.g.
it's a list or a string), the installer refuses to clobber it and exits.
Convert your `extensions:` to a mapping shape and rerun.

## Goose version compatibility

Any Goose **1.x**. The extension schema (`extensions.<name>` mapping with
`type: "stdio"`, `cmd`, `args`, `envs`) has been stable across the 1.x
line. If you're on a `0.x` build, upgrade Goose first.

## Privacy

brainctl runs entirely on your machine. `brain.db` is plaintext SQLite at
rest — don't commit it. Wrap any text in `<private>…</private>` before
passing it to brainctl tools and it will be stripped via
`agentmemory.lib.privacy.redact_private` (where supported).

## License

MIT. Part of the [brainctl](https://github.com/TSchonleber/brainctl)
project.
