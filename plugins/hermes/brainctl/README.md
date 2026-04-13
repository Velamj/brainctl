# brainctl Memory Provider for Hermes Agent

A [Hermes Agent](https://hermes-agent.nousresearch.com) memory provider that
wraps the [brainctl](https://pypi.org/project/brainctl/) cognitive memory system:
SQLite-backed long-term memory with FTS5 full-text search, optional vector
recall, a knowledge graph, affect tracking, and session handoffs.

## Install

> **Important:** Hermes loads third-party memory providers from
> `~/.hermes/plugins/<name>` (the general user-plugin directory), **not** from
> `~/.hermes/plugins/memory/<name>`. Installing into `plugins/memory/` will
> silently fail to register with current Hermes builds — see the discovery
> workaround below.

1. Install brainctl into the **Hermes Python environment**. Hermes runs under
   its own interpreter, so any package it imports at runtime (including
   `brainctl` and its deps) must live in Hermes's venv, not in your shell's
   default `pip`:
   ```bash
   # Activate the venv Hermes was installed with, then:
   pip install 'brainctl>=1.1.2'
   # Optional vector recall (requires Ollama running locally):
   pip install 'brainctl[vec]'
   ```
   If you installed Hermes from source, this is typically the venv in
   `~/.hermes/venv` or the one you created for `hermes-agent`. Running
   `pip install brainctl` outside that venv will leave Hermes unable to import
   the provider even after the plugin files are in place.
2. Drop this plugin into the Hermes user-plugin directory:
   ```bash
   # Intended install path (matches Hermes's general user-plugin docs):
   cp -r plugins/hermes/brainctl ~/.hermes/plugins/brainctl
   # or symlink if developing locally:
   ln -s "$(pwd)/plugins/hermes/brainctl" ~/.hermes/plugins/brainctl
   ```
   Do **not** copy into `~/.hermes/plugins/memory/brainctl` — that path is not
   scanned for user plugins and the provider will not be detected.
3. **Workaround for Hermes memory-provider discovery bug** (hermes-agent#4956):
   current Hermes versions only scan the bundled source-tree
   `plugins/memory/` directory when running `hermes memory setup` /
   `hermes memory status`, so a user-space plugin in `~/.hermes/plugins/` is
   not seen. Until that is fixed upstream, also symlink the plugin into the
   bundled memory-plugin directory so Hermes finds it:
   ```bash
   mkdir -p ~/.hermes/hermes-agent/plugins/memory
   ln -s ~/.hermes/plugins/brainctl \
         ~/.hermes/hermes-agent/plugins/memory/brainctl
   ```
   After that, `hermes memory status` should list brainctl as installed and
   available.
4. Activate it:
   ```bash
   hermes memory setup
   # choose: brainctl
   ```
   or edit your Hermes config:
   ```yaml
   memory:
     provider: brainctl
   ```

## Config

Config lives at `$HERMES_HOME/brainctl/config.json` (profile-scoped). All
fields are optional — sensible defaults are used.

| Key                    | Default                              | Description |
|------------------------|--------------------------------------|-------------|
| `db_path`              | `$HERMES_HOME/brainctl/brain.db`     | Path to the SQLite brain. Falls back to `$BRAIN_DB` env var. |
| `agent_id`             | `hermes`                             | Recorded on every write for multi-agent scoping. |
| `memory_mode`          | `hybrid`                             | `context` (auto-inject only), `tools` (tools only), or `hybrid` (both). |
| `recall_method`        | `search`                             | `search` (FTS5), `vsearch` (vector), or `think` (spreading activation). |
| `recall_limit`         | `8`                                  | Max memories returned per auto-recall. |
| `auto_recall`          | `true`                               | Auto-prefetch context before each turn. |
| `auto_retain`          | `true`                               | Auto-retain completed turns. |
| `retain_category`      | `conversation`                       | Category assigned to auto-retained turns. |
| `retain_every_n_turns` | `1`                                  | Batch retains every N turns. |
| `session_bookends`     | `true`                               | Call `brain.orient()` at start and `brain.wrap_up()` at session end. |
| `mirror_memory_md`     | `true`                               | Mirror built-in `MEMORY.md` / `USER.md` writes into `brain.db`. |
| `project`              | `""`                                 | Optional project scope for events & handoffs. |

Environment-variable fallbacks (used only when no config file exists):
`BRAIN_DB`, `BRAINCTL_AGENT_ID`, `BRAINCTL_RECALL_METHOD`,
`BRAINCTL_RECALL_LIMIT`, `BRAINCTL_MEMORY_MODE`, `BRAINCTL_RETAIN_CATEGORY`,
`BRAINCTL_RETAIN_EVERY_N_TURNS`.

## What it exposes to the model

When `memory_mode` is `tools` or `hybrid`, these tool calls are registered:

- `brainctl_remember(content, category?, tags?, confidence?)` — store durable facts.
- `brainctl_search(query, limit?)` — FTS5 recall.
- `brainctl_think(query, hops?, top_k?)` — spreading-activation associative recall.
- `brainctl_log(summary, event_type?, project?, importance?)` — log an event.
- `brainctl_entity(name, entity_type, observations?)` — upsert a knowledge-graph node.
- `brainctl_decide(title, rationale, project?)` — record a decision.
- `brainctl_handoff(goal, current_state, open_loops, next_step, project?)` — session continuity packet.

When `memory_mode` is `context` or `hybrid`, relevant memories are
auto-recalled before each turn and injected into the system prompt. The
first turn of every session also receives an **orient snapshot** — pending
handoff, active triggers, and recent events — courtesy of `brain.orient()`.

## Hooks

- `on_session_end` — flushes pending retains and calls `brain.wrap_up()` to
  log a session_end event and create a handoff packet for next time.
- `on_pre_compress` — stashes a summary of about-to-be-compressed context as
  a `lesson` memory so it can be recovered later via `brainctl_search`.
- `on_memory_write` — mirrors Hermes built-in `MEMORY.md` / `USER.md` writes
  into `brain.db` under the `identity` / `user` categories.

## Notes

- Subagents and cron contexts (`agent_context != "primary"`) run **read-only**
  so their transient activity does not pollute the long-term store.
- All storage is scoped to `hermes_home` — no hardcoded `~/.hermes` paths.
- Vector recall silently falls back to FTS5 when `sqlite-vec` / Ollama are not
  available, so it is safe to set `recall_method: vsearch` unconditionally.
