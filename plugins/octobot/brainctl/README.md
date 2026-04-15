# brainctl x OctoBot

> **Status:** Placeholder — not yet implemented. Tracked in
> [`plugins/TRADING_INTEGRATIONS.md`](../../TRADING_INTEGRATIONS.md#priority-2--pro-grade-trading-bots).

## Why this integration matters

[OctoBot](https://github.com/Drakkar-Software/OctoBot) is the
biggest open-source retail crypto trading bot outside Hummingbot by
install count, with a polished web UI and a first-class **tentacle**
plugin system designed exactly for third-party integrations like
this one. Shipping brainctl as a tentacle lets thousands of retail
OctoBot users opt in with a one-click install from the tentacle
manager.

## Planned integration pattern

**Tentacle package**. Ship a `brainctl_service` tentacle under
OctoBot's `Services` category (or `Strategies`, TBD after reading
the tentacle spec). The tentacle:

- Subscribes to OctoBot's lifecycle channels
  (`bot-started`, `bot-stopping`, `trade-executed`,
  `evaluation-changed`).
- Forwards each event to brainctl via `Brain.log()` /
  `decision_add`.
- Exposes a config panel for `BRAIN_DB` path + agent ID.

Distributed via a tentacle JSON manifest so users install with
`pip install -e` or OctoBot's tentacle manager.

## Upstream

- https://github.com/Drakkar-Software/OctoBot
- https://www.octobot.cloud
- https://developer-docs.octobot.online/pages/guides/developers/tentacles.html

## Contributing

PRs welcome. Use `plugins/freqtrade/brainctl/mixin.py` as the
handler reference and OctoBot's tentacle template for the package
shape.
