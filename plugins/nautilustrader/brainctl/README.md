# brainctl x NautilusTrader

> **Status:** Placeholder — not yet implemented. Tracked in
> [`plugins/TRADING_INTEGRATIONS.md`](../../TRADING_INTEGRATIONS.md#priority-2--pro-grade-trading-bots).

## Why this integration matters

[NautilusTrader](https://nautilustrader.io) is a high-performance,
event-driven algorithmic trading platform with a Rust core and
Python API. It's the serious quant's choice — built for
high-frequency, multi-venue, backtest/live parity — and has been
gaining ground in crypto quant shops that outgrew Freqtrade and
Jesse.

Nautilus's Actor model is a clean fit for brainctl: every Actor
already exposes `on_start` / `on_stop` / `on_event` lifecycle
hooks that map 1:1 to brainctl's orient/log/wrap_up surface.

## Planned integration pattern

**Actor base class** — ship a `BrainctlActor` extending
`nautilus_trader.common.actor.Actor` that users subclass instead of
`Actor` directly. Wires:

- `on_start` -> `Brain.orient()`
- `on_stop` -> `Brain.wrap_up()`
- `on_event` -> `Brain.log()`
- `on_order_filled` -> `decision_add` with the fill rationale

Same pattern as `plugins/jesse/brainctl/mixin.py`, adapted to
Nautilus's Actor API.

## Upstream

- https://github.com/nautechsystems/nautilus_trader
- https://nautilustrader.io
- https://docs.nautilustrader.io

## Contributing

PRs welcome. Use `plugins/jesse/brainctl/mixin.py` as the template.
