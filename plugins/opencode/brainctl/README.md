# brainctl for OpenCode

Persistent memory for [OpenCode](https://github.com/anomalyco/opencode)
(formerly sst/opencode — 145k stars) powered by
[brainctl](https://pypi.org/project/brainctl/).

OpenCode supports **both MCP servers and a TypeScript plugin / hook
system** — the closest surface to Claude Code we've integrated with. This
plugin uses both:

- **MCP** registers `brainctl-mcp` so the model has 200+ memory tools at
  its fingertips (`memory_add`, `memory_search`, `decision_add`,
  `entity_*`, `event_add`, `agent_orient`, `agent_wrap_up`, …).
- **TypeScript hooks** automatically orient at session start, log every
  tool call as an observation, and write a wrap-up handoff at session
  idle / delete — without the model having to remember to.

No long-running worker, no HTTP port, no extra LLM calls. One SQLite file.

## What you get

| OpenCode hook        | brainctl call                                        |
|----------------------|------------------------------------------------------|
| `session.created`    | `agent_orient` snapshot, plus a `session_start` event |
| `tool.execute.after` | `event_add` (`observation` / `error`) per tool call  |
| `session.idle`       | `agent_wrap_up` (deduped — see below)                |
| `session.deleted`    | `agent_wrap_up` if the idle path hadn't fired        |

Plus all 200+ brainctl MCP tools available to the model directly.

## Install

```bash
pip install 'brainctl>=2.4.2'
python3 plugins/opencode/brainctl/install.py
```

The installer is idempotent. Re-run it whenever you update the plugin.

Dry-run first if you're nervous:

```bash
python3 install.py --dry-run
```

### Scopes

Global (default — installs into `~/.config/opencode/` ):

```bash
python3 install.py
```

Project-local (installs into `./opencode.json` + `./.opencode/plugins/`):

```bash
python3 install.py --scope project
```

Custom config path:

```bash
python3 install.py --config /path/to/opencode.json
```

### Subset installs

Only register the MCP server (skip the TS hook plugins):

```bash
python3 install.py --mcp-only
```

Only install the TS hook plugins (skip the MCP merge — useful if you've
already wired brainctl-mcp another way):

```bash
python3 install.py --plugins-only
```

### Overwrite & uninstall

```bash
python3 install.py --force          # overwrite existing files / mcp entries
python3 install.py --uninstall      # remove the mcp block + delete our 3 .ts plugins
python3 install.py --uninstall --mcp-only
python3 install.py --uninstall --plugins-only
```

### Preflight

The installer checks that `brainctl-mcp` and `brainctl` are on PATH and
warns (does not fail) if not. Pass `--no-validate` to suppress.

## Supported hook events

This plugin ships handlers for the three events that line up with the
brainctl session lifecycle:

| OpenCode event       | What we do                                   | File                       |
|----------------------|----------------------------------------------|----------------------------|
| `session.created`    | `agent_orient` + `session_start` event       | `plugins/brainctl-orient.ts` |
| `tool.execute.after` | `event_add` (observation / error)            | `plugins/brainctl-tool-log.ts` |
| `session.idle`       | `agent_wrap_up` (deduped via tempfile flag)  | `plugins/brainctl-wrap-up.ts`  |
| `session.deleted`    | `agent_wrap_up` (always, if idle hadn't run) | `plugins/brainctl-wrap-up.ts`  |

### Why dedupe `session.idle`?

Unlike Gemini CLI's `SessionEnd` or Claude Code's `Stop`, OpenCode's
`session.idle` fires every time the user pauses — not just at the very
end. Without dedupe we'd write a handoff packet on every pause, which
would spam `brain.db` and confuse the next `agent_orient`.

We track wrap state in a tempfile flag at:

    ${TMPDIR:-/tmp}/brainctl-opencode-wrapped/<short-session-id>.flag

`session.deleted` always wins (it's the true terminal event). On
`session.idle`, we only wrap if the flag is absent and set it after.
Old flags (>24h) are cleaned up opportunistically.

### Other hook events you might want

OpenCode exposes a much richer hook surface — see
<https://opencode.ai/docs/plugins/>. If you want to capture more, copy
one of our `.ts` files and listen to:

| Event family | Examples |
|--------------|----------|
| File         | `file.edited`, `file.watcher.updated` |
| Message      | `message.updated`, `message.removed`, `message.part.updated`, `message.part.removed` |
| Permission   | `permission.asked`, `permission.replied` |
| Tool         | `tool.execute.before` (mutate args, gate calls) |
| Command      | `command.executed` |
| LSP          | `lsp.client.diagnostics`, `lsp.updated` |
| TUI          | `tui.toast.show`, `tui.command.execute`, `tui.prompt.append` |
| Session      | `session.compacted`, `session.error`, `session.updated`, `session.status` |

The pattern is always the same:

```ts
import type { Plugin } from "@opencode-ai/plugin";

export const MyPlugin: Plugin = async ({ client, $, directory }) => {
  return {
    "tool.execute.before": async (input, output) => {
      try {
        // do something — call brainctl, mutate output.args, etc.
      } catch (err) {
        // never break the session
      }
    },
  };
};

export default MyPlugin;
```

## How the hooks talk to brainctl

Each `.ts` plugin shells out to the `brainctl` CLI via Bun's `$` template
shell helper:

```ts
await $`brainctl --agent ${agentId} orient --project ${project} --compact`
  .quiet()
  .nothrow();
```

Why CLI shell-out instead of MCP? OpenCode's plugin runtime doesn't
currently expose a documented way to call MCP tools from plugin code —
the `client` argument's MCP surface isn't published. Shelling out to the
already-installed `brainctl` CLI is stable, scriptable, and dependency-
free. Each TS file has a `TODO(client.mcp.tool)` comment marking where
to switch over if/when OpenCode adds plugin-side MCP-tool invocation.

The MCP block in `opencode.json` registers `brainctl-mcp` so the *model*
can call brainctl tools directly during a turn — that's the primary
high-bandwidth path. The hooks are the low-bandwidth, automatic
lifecycle / telemetry layer that runs whether the model thinks to call
brainctl or not.

## Graceful degradation

Every hook is wrapped in try/catch. If `brainctl` isn't installed, if
`brain.db` is missing, if the CLI returns non-zero, if Bun's `$` blows
up — the OpenCode session continues unaffected. The worst case is you
lose the events / handoff for that one run. Errors are logged via
`client.app.log` (if available) at level `warn` so you can spot them in
the OpenCode service log.

## Privacy & safety

- The `tool.execute.after` hook stores the tool name + a one-line input
  summary capped at ~200 chars. It never persists the full tool output
  (we don't want file contents leaking into `brain.db`).
- The orient hook surfaces context as a `client.app.log` info message,
  not by injecting it into the chat — so we don't unilaterally rewrite
  the system prompt.
- `brain.db` lives at `${HOME}/agentmemory/db/brain.db` by default
  (override via `BRAINCTL_DB` in the MCP environment block). Plaintext
  SQLite at rest — don't store secrets in it.

## Compatibility

- OpenCode **v1.x** (anomalyco/opencode) — tested against the documented
  plugin / MCP schema as of 2026-04. The plugin reads payload fields
  defensively because OpenCode's per-event input/output shapes are not
  fully published.
- brainctl **>= 2.4.2** (uses `brainctl orient`, `brainctl wrap-up`,
  `brainctl event add`, all stable since 2.0).
- Python **>= 3.11** for the installer.
- Bun ships inside OpenCode — no extra runtime needed.

## Files in this directory

```
plugins/opencode/brainctl/
  install.py                 # idempotent installer (also handles --uninstall)
  opencode.json.fragment     # the JSON block merged into opencode.json
  README.md                  # this file
  AGENTS.md                  # per-session context for the model
  plugins/
    brainctl-orient.ts       # session.created
    brainctl-wrap-up.ts      # session.idle / session.deleted
    brainctl-tool-log.ts     # tool.execute.after
```

## License

MIT. Part of the [brainctl](https://github.com/TSchonleber/brainctl) project.
