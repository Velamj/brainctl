# brainctl session context

This Gemini CLI session has [brainctl](https://github.com/TSchonleber/brainctl)
available as an MCP server (199 tools, SQLite-backed, local-first).

## At session start

The `SessionStart` hook calls `mcp__brainctl__agent_orient` and injects the
returned handoff packet, recent events, and top memories into your context.
If you don't see that block, call `mcp__brainctl__agent_orient` yourself
with `agent_id="gemini:<project>"` and `project="<project>"` before starting
substantive work.

## During the session

Write as you go — don't batch at the end:

- **Decisions:** `mcp__brainctl__decision_add` for any non-trivial choice
  with its `rationale`. Always include `project`.
- **Memories:** `mcp__brainctl__memory_add` for durable facts. Pick a
  `category` from `convention | decision | environment | identity |
  integration | lesson | preference | project | user`. Use
  `source="human_verified"` when the user explicitly confirmed it.
- **Entities:** `mcp__brainctl__entity_create` for people, projects,
  services, tools. Use `mcp__brainctl__entity_observe` to append facts to
  existing entities — don't make duplicates.
- **Events:** `mcp__brainctl__event_add` with `importance >= 0.8` for
  things that retroactively matter (triggers labile-window rescue).

## At session end

The `SessionEnd` hook calls `Brain.wrap_up` automatically. If you want a
richer handoff, call `mcp__brainctl__agent_wrap_up` directly with a real
`summary`, `goal`, `open_loops`, and `next_step` before the hook fires.
