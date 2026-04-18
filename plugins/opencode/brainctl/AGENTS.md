# brainctl session context

This OpenCode session has [brainctl](https://github.com/TSchonleber/brainctl)
available as an MCP server (200+ tools, SQLite-backed, local-first), plus
three lifecycle plugins that run automatically:

- `brainctl-orient.ts` — on `session.created`, pulls the prior handoff,
  recent events, and top memories.
- `brainctl-tool-log.ts` — on `tool.execute.after`, logs each tool call as
  an `observation` event (`error` if it failed).
- `brainctl-wrap-up.ts` — on `session.idle` / `session.deleted`, writes a
  handoff packet for the next session (deduped via a tempfile flag).

## Common operations

Write as you go — don't batch at the end. The MCP tools are exposed as
`brainctl_*` (or `mcp__brainctl__*` depending on OpenCode's namespacing):

- **Decisions:** `decision_add` for any non-trivial choice with its
  `rationale`. Always include `project`.
- **Memories:** `memory_add` for durable facts. Pick a `category` from the
  enum below. Use `source="human_verified"` when the user explicitly
  confirmed it.
- **Entities:** `entity_create` for people, projects, services, tools.
  `entity_observe` to append facts to existing entities — don't make
  duplicates.
- **Events:** `event_add` with `importance >= 0.8` for things that
  retroactively matter (triggers labile-window rescue).
- **Search:** `memory_search`, `vsearch`, `entity_search`,
  `federated_search`.
- **Continuity:** `agent_orient` (start), `agent_wrap_up` (end),
  `handoff_add` (richer mid-session handoff).

## Enums

- **memory category:** `convention | decision | environment | identity |
  integration | lesson | preference | project | user`
- **event_type:** `artifact | decision | error | handoff | memory_promoted
  | memory_retired | observation | result | session_start | session_end |
  stale_context | task_update | warning`
- **entity_type:** `agent | concept | document | event | location |
  organization | other | person | project | service | tool`

## Comparison

The brainctl plugin set for OpenCode is documented at
<https://github.com/TSchonleber/brainctl/tree/main/plugins/opencode/brainctl>.
For a head-to-head with the equivalent Gemini CLI / Claude Code plugins,
see the comparison at <https://brainctl.ai/integrations>.
