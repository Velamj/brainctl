# brainctl x Virtuals GAME SDK

> **Status:** Placeholder — not yet implemented. Tracked in
> [`plugins/TRADING_INTEGRATIONS.md`](../../TRADING_INTEGRATIONS.md#priority-1--crypto-native-agent-frameworks).

## Why this integration matters

The [Virtuals Protocol](https://whitepaper.virtuals.io) GAME SDK
(Generative Agent Model Engine) powers the largest crypto-AI agent
ecosystem by market cap — Luna, aixbt, and the broader $VIRTUAL
cohort. Virtuals agents ship with long-running personalities,
onchain identities, and multi-worker task loops, but they have no
shared memory substrate: each agent rolls its own state store, or
goes without.

brainctl is a drop-in fix: a shared, queryable, graph-aware memory
every GAME agent can read from and write to.

## Planned integration pattern

**Custom worker wrapper + function registry.** GAME's agent runtime
takes a list of Workers, each exposing Functions. This plugin ships:

- A TypeScript package `@brainctl/game-worker` exporting a
  `createBrainctlWorker()` factory that returns a GAME-compatible
  Worker with functions `orient`, `remember`, `recall`, `decide`,
  and `wrapUp`.
- A Python mirror for the `game-python` SDK with the same surface.

Under the hood both implementations shell out to `brainctl-mcp` or
the `brainctl` CLI, so they inherit the full tool surface without
re-implementing it.

## Upstream

- https://github.com/game-by-virtuals
- https://whitepaper.virtuals.io
- https://docs.game.virtuals.io

## Contributing

PRs welcome. Use `plugins/eliza/brainctl/src/` as the TS template
and match the shape spelled out in
[`plugins/TRADING_INTEGRATIONS.md`](../../TRADING_INTEGRATIONS.md).
