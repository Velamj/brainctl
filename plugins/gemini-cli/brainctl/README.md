# brainctl for Gemini CLI

Persistent memory for [Gemini CLI](https://github.com/google-gemini/gemini-cli)
powered by [brainctl](https://pypi.org/project/brainctl/). Every session
starts with the handoff packet from your last run injected as context,
every tool call is journaled as an observation event, and every session
ends with a new handoff packet written to `brain.db` — all via Gemini CLI's
native lifecycle hooks plus the brainctl MCP server (199 tools).

No long-running worker, no HTTP port, no LLM calls. One SQLite file.

## What you get

| Gemini CLI hook | brainctl write |
|---|---|
| **SessionStart** | `Brain.orient()` snapshot (handoff + recent events + triggers + top memories) injected as `additionalContext` |
| **AfterTool** | Each tool call logged as `observation` / `error` event (tool name + short input summary, no full outputs) |
| **SessionEnd** | `Brain.wrap_up()` creates a pending handoff packet for the next session |

Plus all 199 brainctl MCP tools available to Gemini directly via the
`mcpServers.brainctl` entry in `gemini-extension.json`.

## Install

```bash
pip install 'brainctl>=2.2.4'
python3 plugins/gemini-cli/brainctl/install.py
```

The installer copies the plugin into `~/.gemini/extensions/brainctl/` (or
`$GEMINI_HOME/extensions/brainctl/`) and is idempotent — rerunning is safe.

Dry-run first if you're nervous:

```bash
python3 plugins/gemini-cli/brainctl/install.py --dry-run
```

MCP-only (no hooks, just the 199 tools merged into `~/.gemini/settings.json`):

```bash
python3 plugins/gemini-cli/brainctl/install.py --mcp-only
```

Uninstall:

```bash
python3 plugins/gemini-cli/brainctl/install.py --uninstall
python3 plugins/gemini-cli/brainctl/install.py --mcp-only --uninstall
```

## Supported hook events

Gemini CLI's hook event names differ from Claude Code's; this plugin maps
them as follows:

| Gemini event   | Claude Code equivalent | Hook script              |
|----------------|------------------------|--------------------------|
| `SessionStart` | `SessionStart`         | `hooks/session_start.py` |
| `SessionEnd`   | `SessionEnd`           | `hooks/session_end.py`   |
| `AfterTool`    | `PostToolUse`          | `hooks/post_tool_use.py` |

### Why no `UserPromptSubmit` equivalent?

Gemini CLI does not ship a top-level `UserPromptSubmit` hook event. The
closest analogue is `BeforeModel`, but that fires on every model turn
(including tool-loop continuations), not specifically when the user types.
We chose to skip the prompt-logging hook here rather than over-fire on
intermediate model turns. If Gemini CLI adds a true user-prompt event
later, this plugin will pick it up via a follow-up release.

## MCP tool surface

The `gemini-extension.json` registers `brainctl-mcp` (the canonical
pip-installed binary) under `mcpServers.brainctl`. All 199 MCP tools then
appear to Gemini under the `mcp__brainctl__*` prefix. Highlights:

- `agent_orient`, `agent_wrap_up`, `handoff_add` — session continuity
- `memory_add`, `memory_search`, `vsearch` — durable facts + retrieval
- `decision_add` — log non-trivial choices
- `entity_create`, `entity_observe`, `entity_relate` — knowledge graph
- `event_add` — timestamped session journal
- `infer`, `reason`, `think` — built-in reflexion / reasoning loops

See the `GEMINI.md` context file for the at-a-glance guide injected into
each session.

## Config

Environment variables (all optional):

| Variable | Default | Purpose |
|---|---|---|
| `BRAINCTL_DB` | `~/agentmemory/db/brain.db` | Override the SQLite brain path (set by manifest; also honored as `BRAIN_DB` for parity with the Claude Code plugin) |
| `BRAINCTL_AGENT_ID` | `gemini:<cwd-basename>` | Stable agent ID for the session |
| `BRAINCTL_PROJECT` | `<cwd-basename>` | Project scope for events and handoffs |

## Privacy

Wrap any text in `<private>…</private>` tags and it will be stripped
before being written to the brain. Applies to anything routed through
`agentmemory.lib.privacy.redact_private` — including tool input summaries.

If brainctl's privacy module isn't importable for some reason, the hook
falls back to raw pass-through so it never crashes the session.

## Graceful degradation

Every hook is a no-op if brainctl isn't installed, if `brain.db` is
missing, or if anything throws. **Your Gemini CLI session never breaks**
because of a memory system glitch — the worst case is you lose the events
from that run. Errors are logged to stderr with the prefix
`[brainctl-hook]` so you can spot them in your terminal scrollback.

## Known limitations

- **No `UserPromptSubmit` hook** — see "Why no `UserPromptSubmit`" above.
  Use `mcp__brainctl__event_add` from the model turn if you want explicit
  prompt logging.
- **No LLM summarization in `wrap_up`** — the handoff is synthesized from
  structured event rows, not a Gemini call. Call
  `mcp__brainctl__agent_wrap_up` manually with a richer `summary` if you
  need it.
- **No full tool-output capture** — `AfterTool` stores the tool name and
  a short input preview (~200 chars). Enough for forensics, not enough to
  leak file contents into `brain.db`.
- **`tool_response` shape may vary across Gemini CLI versions** — the
  hook reads it defensively (`is_error`, `error`, `status == "error"`)
  and treats absence as success.

## Compatibility

- Gemini CLI **≥ 0.26.0** (hooks shipped in 0.26.0).
- brainctl **≥ 2.2.4**.
- Python **≥ 3.11**.

## License

MIT. Part of the [brainctl](https://github.com/TSchonleber/brainctl) project.
