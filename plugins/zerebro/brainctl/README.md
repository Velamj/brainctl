# brainctl x ZerePy (Zerebro)

> **Status:** Placeholder — not yet implemented. Tracked in
> [`plugins/TRADING_INTEGRATIONS.md`](../../TRADING_INTEGRATIONS.md#priority-1--crypto-native-agent-frameworks).

## Why this integration matters

[ZerePy](https://github.com/blorm-network/ZerePy) is the reference
agent framework for the $ZEREBRO cohort — Python/TS-first, onchain,
and heavily wired into social surfaces (Twitter/X, Discord,
Farcaster). It's the closest thing to Eliza on the Solana side and
targets exactly the audience brainctl-launch is courting.

ZerePy agents persist connections (Twitter, RPC, LLM) but lose all
reasoning context on restart. brainctl plugs the gap with a shared
memory brain across sessions and agents.

## Planned integration pattern

**Plugin package** — same shape as `plugins/eliza/brainctl/src/`.
Registers brainctl as a ZerePy connection type with actions:

- `brainctl.orient` — load context on agent start
- `brainctl.remember` — store a memory
- `brainctl.decide` — log a decision with rationale
- `brainctl.search` — retrieve recent memories
- `brainctl.wrap_up` — emit a session summary on shutdown

Uses the same `Brain` Python API bindings as the Eliza plugin for
consistency.

## Upstream

- https://github.com/blorm-network/ZerePy

## Contributing

PRs welcome. Use `plugins/eliza/brainctl/` as the template and match
the shape in
[`plugins/TRADING_INTEGRATIONS.md`](../../TRADING_INTEGRATIONS.md).
