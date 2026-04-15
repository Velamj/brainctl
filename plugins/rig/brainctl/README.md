# brainctl x Rig

> **Status:** Placeholder — not yet implemented. Tracked in
> [`plugins/TRADING_INTEGRATIONS.md`](../../TRADING_INTEGRATIONS.md#priority-1--crypto-native-agent-frameworks).

## Why this integration matters

[Rig](https://rig.rs) (from 0xPlaygrounds) is the Rust-first LLM
agent framework picked up fast by high-performance crypto, MEV, and
arbitrage bots that can't afford Python's GIL or its dependency
sprawl. It's the only serious agent framework in the Rust ecosystem
with a real tool-use abstraction.

brainctl has no Rust story today — everything ships Python or TS.
A first-class Rig integration is the only path to agents built on
Rig's `Agent` + `Tool` traits.

## Planned integration pattern

**Native Rust crate.** Ship a `brainctl-rig` crate implementing
Rig's [`Tool`](https://docs.rs/rig-core/latest/rig/tool/trait.Tool.html)
trait for each of brainctl's primitive ops (`orient`, `memory_add`,
`decision_add`, `search`, `wrap_up`).

First cut shells out to the `brainctl` CLI via
`std::process::Command` — simple, robust, mirrors how the
mixin-pattern plugins work. A future v0.2 could link against a
pure-Rust SQLite backend for in-process memory once one exists.

## Upstream

- https://github.com/0xPlaygrounds/rig
- https://rig.rs
- https://docs.rs/rig-core

## Contributing

PRs welcome. No existing brainctl plugin is in Rust — this will be
the reference. Match the CLI-shell-out shape used by
`plugins/freqtrade/brainctl/mixin.py` and document the crate
publishing flow in its own `README.md`.
