# brainctl x Coinbase AgentKit

> **Status:** Placeholder — not yet implemented. Tracked in
> [`plugins/TRADING_INTEGRATIONS.md`](../../TRADING_INTEGRATIONS.md#priority-1--crypto-native-agent-frameworks).

## Why this integration matters

[Coinbase AgentKit](https://github.com/coinbase/agentkit) is Coinbase's
official framework for onchain AI agents: wallets, onramp, DEX swaps,
ERC-20 / ERC-721 / ERC-1155 ops, and gasless transactions via the
Coinbase Developer Platform (CDP). It ships in TypeScript and Python
and is the default recommendation in every CDP tutorial, Base
hackathon, and "build an onchain agent" guide.

Onchain agents running AgentKit today have no persistent memory
across sessions — wallets persist, but the agent's reasoning,
decisions, postmortems, and handoffs don't. brainctl is exactly
that missing primitive.

## Planned integration pattern

Dual shape, mirroring AgentKit's own TS + Py split:

- **Python:** a native `BrainctlActionProvider` that extends
  `coinbase_agentkit.action_providers.ActionProvider` and registers
  `orient`, `memory_add`, `decision_add`, `search`, and `wrap_up` as
  AgentKit Actions. Users add it to their `AgentKit` instance
  alongside `wallet_action_provider` and friends.
- **TypeScript:** use the existing **MCP installer pattern**
  (`plugins/codex/brainctl/install.py`) to register `brainctl-mcp`
  as an MCP server in the user's AgentKit TS agent config, letting
  the agent invoke brainctl's tools via MCP.

## Upstream

- https://github.com/coinbase/agentkit
- https://docs.cdp.coinbase.com/agentkit/docs/welcome

## Contributing

PRs welcome. Use `plugins/codex/brainctl/` (MCP installer) and
`plugins/jesse/brainctl/mixin.py` (native Python class) as templates.
Match the shape spelled out in
[`plugins/TRADING_INTEGRATIONS.md`](../../TRADING_INTEGRATIONS.md).
