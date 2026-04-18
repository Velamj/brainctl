# brainctl session context

This Goose session has [brainctl](https://github.com/TSchonleber/brainctl)
available as the `brainctl` MCP extension (201 tools, SQLite-backed,
local-first). Tool calls are exposed as `brainctl__*` in Goose.

## At session start

Call `brainctl__agent_orient` with `agent_id="goose:<project>"` and
`project="<project>"`. It returns the pending handoff packet from your
previous run plus recent events, active triggers, and top memories — read
it before you do substantive work so you resume with full context.

## During the session — write as you go, not at the end

- **Decisions:** `brainctl__decision_add` for any non-trivial choice with
  its `rationale`. Always include `project`.
- **Memories:** `brainctl__memory_add` for durable facts. Pick a
  `category` from `convention | decision | environment | identity |
  integration | lesson | preference | project | user`. Use
  `source="human_verified"` when the user explicitly confirmed it.
- **Entities:** `brainctl__entity_create` for people, projects,
  services, tools, concepts. Append facts via `brainctl__entity_observe`
  rather than creating duplicates.
- **Events:** `brainctl__event_add` with `importance >= 0.8` for things
  that retroactively matter — that bumps a labile-window rescue that
  retroactively tags memories from the prior 2 hours as important.

## At session end

Call `brainctl__agent_wrap_up` with `agent_id`, `project`, `summary`,
`goal`, `open_loops`, and `next_step`. This logs a session_end event and
creates the handoff packet the next session orients from.

Goose has no native session-end hook, so this must be explicit — don't
end a Goose session without `wrap_up`.
