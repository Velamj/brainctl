# brainctl session context (Pi)

This Pi session has [brainctl](https://github.com/TSchonleber/brainctl)
available **via the `pi-mcp-adapter` proxy**. Pi deliberately ships without
built-in MCP support, so all 201 brainctl tools are reached through the
adapter's single `mcp` tool (lazy-loaded, ~200 tokens of tool surface).

## How to call brainctl tools

The adapter exposes ONE tool named `mcp`. To call any brainctl tool, pass
`tool` (the brainctl tool name without prefix) and `args` (a JSON string):

```
mcp({ tool: "agent_orient", args: "{\"agent_id\": \"pi:<project>\", \"project\": \"<project>\"}" })
mcp({ tool: "memory_add",   args: "{\"content\": \"...\", \"category\": \"project\", \"scope\": \"project:<name>\"}" })
mcp({ tool: "decision_add", args: "{\"title\": \"...\", \"rationale\": \"...\", \"project\": \"<name>\"}" })
mcp({ tool: "event_add",    args: "{\"summary\": \"...\", \"event_type\": \"observation\", \"importance\": 0.5}" })
mcp({ tool: "agent_wrap_up",args: "{\"agent_id\": \"pi:<project>\", \"summary\": \"...\", \"open_loops\": \"...\", \"next_step\": \"...\"}" })
```

If `directTools` is enabled in the adapter config, individual brainctl tools
may also appear as first-class entries (prefix configurable: `server`, `short`,
or `none`). Check `mcp.json` settings if you see e.g. `brainctl_agent_orient`.

## Session lifecycle

Pi has no SessionStart / SessionEnd hooks bound to brainctl in v1 — orient
and wrap_up by calling them yourself:

- **Start of session:** `mcp({ tool: "agent_orient", args: "..." })` — read pending handoffs and act on open loops.
- **End of session:** `mcp({ tool: "agent_wrap_up", args: "..." })` — log session_end + emit a handoff packet for next time.

## Categories & types (enums)

- `memory_add.category`: `convention | decision | environment | identity | integration | lesson | preference | project | user`
- `event_add.event_type`: `observation | result | decision | error | handoff | task_update | artifact | session_start | session_end | warning | memory_promoted | memory_retired | stale_context`
- `entity_create.entity_type`: `agent | concept | document | event | location | organization | other | person | project | service | tool`

## Common ops

- Recall: `mcp({ tool: "memory_search", args: "{\"query\": \"...\", \"limit\": 10}" })`
- Vector recall: `mcp({ tool: "vsearch", args: "{\"query\": \"...\", \"k\": 10}" })`
- Append fact to existing entity: `mcp({ tool: "entity_observe", args: "{\"identifier\": \"<name>\", \"observations\": \"...; ...\"}" })`

Use `source: "human_verified"` on `memory_add` when the user explicitly
confirmed a fact (1.0 trust score, easier W(m) gate clearance).
