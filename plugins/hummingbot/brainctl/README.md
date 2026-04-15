# brainctl x Hummingbot

> **Status:** Placeholder — not yet implemented. Tracked in
> [`plugins/TRADING_INTEGRATIONS.md`](../../TRADING_INTEGRATIONS.md#priority-2--pro-grade-trading-bots).

## Why this integration matters

[Hummingbot](https://github.com/hummingbot/hummingbot) is the
most-used open-source market-making and arbitrage bot for crypto
exchanges (CEX + DEX). Every serious retail market maker runs
it. Hummingbot strategies churn through thousands of orders per day
but have no persistent reasoning log across restarts — when a
strategy crashes, its "why did I do that" context dies with it.

brainctl gives Hummingbot users a queryable decision log that
survives restarts, forks, and backtests.

## Planned integration pattern

**Strategy mixin**, same shape as `plugins/freqtrade/brainctl/mixin.py`.

Ship a `BrainctlStrategyMixin` users multiply-inherit into their
Hummingbot strategy:

```python
class MyStrategy(BrainctlStrategyMixin, ScriptStrategyBase):
    ...
```

The mixin wires:

- `on_start` -> `Brain.orient()`
- `on_stop` -> `Brain.wrap_up()`
- `on_tick` (rate-limited) -> periodic status writes
- `did_fill_order` -> `Brain.log()` + `decision_add`

## Upstream

- https://github.com/hummingbot/hummingbot
- https://hummingbot.org/docs

## Contributing

PRs welcome. Use `plugins/freqtrade/brainctl/mixin.py` as the
template.
