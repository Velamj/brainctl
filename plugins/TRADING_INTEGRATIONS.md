# brainctl — Trading & Crypto Agent Integrations Roadmap

brainctl is a persistent-memory substrate for AI agents: a SQLite-backed
brain with FTS5 search, optional vector recall, a knowledge graph, and
session handoffs. This document tracks the integrations we ship, the
ones we plan to ship, and the ones we've consciously left out.

The focus is **crypto-native agent frameworks** and **serious trading
bots**, because brainctl-launch is gearing up for a public launch in
that space. General-purpose agent frameworks (LangGraph, CrewAI, etc.)
are lower priority and tracked separately at the bottom.

---

## Current coverage

| Plugin | Type | Pattern | Status |
|---|---|---|---|
| [`claude-code`](claude-code/brainctl/README.md) | Coding agent | Hook + skill | shipped |
| [`codex`](codex/brainctl/README.md) | Coding agent | MCP installer | shipped |
| [`cursor`](cursor/brainctl/README.md) | Coding agent | MCP installer + rules file | shipped |
| [`openclaw`](openclaw/brainctl/README.md) | Coding agent | Workspace skill injection | shipped |
| [`eliza`](eliza/brainctl/README.md) | Agent framework | TS plugin package | shipped |
| [`hermes`](hermes/brainctl/README.md) | Trading agent | Native | shipped |
| [`freqtrade`](freqtrade/brainctl/README.md) | Trading bot | Strategy mixin | shipped |
| [`jesse`](jesse/brainctl/README.md) | Trading bot | Strategy mixin | shipped |

---

## Three reusable integration patterns

Every plugin brainctl ships falls into one of three shapes. New plugins
should reuse one of these patterns rather than inventing a new one.

### 1. Strategy / Actor mixin (Python base class)

The host framework (freqtrade, jesse) defines a strategy base class.
brainctl ships a mixin that users multiply-inherit from. Lifecycle
methods like `bot_start`, `confirm_trade_entry`, `on_order_filled`, and
`bot_stop` hand off to `Brain.orient()`, `Brain.log()`, `decision_add`,
and `Brain.wrap_up()`.

**Reference:** `plugins/freqtrade/brainctl/mixin.py`,
`plugins/jesse/brainctl/mixin.py`.

**Best for:** Python trading frameworks with a strategy/actor class
users subclass (Hummingbot, NautilusTrader, OctoBot's tentacle system).

### 2. MCP server installer

The host is an MCP client (Claude Code, Codex, Cursor, Windsurf,
Cline). brainctl ships an idempotent installer that merges a
sentinel-wrapped `mcpServers.brainctl` block into the host's config
file. The agent then calls brainctl's ~196 MCP tools natively.

**Reference:** `plugins/codex/brainctl/install.py`,
`plugins/cursor/brainctl/install.py`.

**Best for:** Any coding agent or agent framework that speaks MCP.
Also works as a universal bridge for frameworks that can spawn MCP
servers as tools (Coinbase AgentKit via Python tooling layer, Rig via
a thin wrapper, etc.).

### 3. Hook / skill injection

The host has no MCP, no subclassable strategy class, but does have a
filesystem-level extension surface: shell hooks (Claude Code's
`hooks/*.py`), workspace markdown injection (OpenClaw's
`workspace/skills/`), or plain rule files (Cursor's `.cursor/rules/`).
brainctl ships a template the installer copies into place.

**Reference:** `plugins/claude-code/brainctl/hooks/`,
`plugins/openclaw/brainctl/SKILL.md.template`.

**Best for:** Agents that expose markdown/skill injection or
shell-level lifecycle hooks but no programmatic tool API.

---

## Priority 1 — crypto-native agent frameworks

Ordered by **launch leverage** — how many crypto-AI developers we
reach per PR.

| Plugin | Framework | Language | Pattern | Placeholder |
|---|---|---|---|---|
| [`coinbase-agentkit`](coinbase-agentkit/brainctl/README.md) | [CDP AgentKit](https://github.com/coinbase/agentkit) | TS + Py | MCP bridge + `BrainctlActionProvider` | yes |
| [`virtuals-game`](virtuals-game/brainctl/README.md) | [Virtuals GAME SDK](https://whitepaper.virtuals.io) | TS + Py | Custom worker + function registry | yes |
| [`rig`](rig/brainctl/README.md) | [Rig](https://rig.rs) | Rust | Native `brainctl-rig` crate | yes |
| [`zerebro`](zerebro/brainctl/README.md) | [ZerePy](https://github.com/blorm-network/ZerePy) | TS | TS plugin package | yes |

### Rationale

- **Coinbase AgentKit** is Coinbase's official onchain-agent framework.
  Every CDP tutorial, every Base hackathon, every "build an onchain
  agent" guide points here. Winning this integration means brainctl
  becomes the default memory layer for Coinbase-branded agent
  tutorials. Highest reach per PR.
- **Virtuals GAME SDK** powers the largest crypto-AI agent ecosystem
  by market cap — Luna, aixbt, and the $VIRTUAL cohort. These agents
  have long-running personalities but no shared memory across
  sessions. brainctl is exactly the missing primitive.
- **Rig** is the Rust-first LLM agent framework used by high-perf
  crypto / MEV / arb bots that can't afford Python's GIL. A
  `brainctl-rig` crate is the only path to those users.
- **ZerePy** is the reference stack for the $ZEREBRO cohort — onchain,
  TS-first, Twitter/Discord/Farcaster-native. Same audience profile as
  brainctl-launch's target users.

---

## Priority 2 — pro-grade trading bots

All three use the **strategy-mixin** pattern already proven by
`freqtrade` and `jesse`.

| Plugin | Bot | Audience | Placeholder |
|---|---|---|---|
| [`hummingbot`](hummingbot/brainctl/README.md) | [Hummingbot](https://github.com/hummingbot/hummingbot) | Retail MM + arb | yes |
| [`nautilustrader`](nautilustrader/brainctl/README.md) | [NautilusTrader](https://nautilustrader.io) | Quant shops, HFT | yes |
| [`octobot`](octobot/brainctl/README.md) | [OctoBot](https://github.com/Drakkar-Software/OctoBot) | Retail algo | yes |

### Rationale

- **Hummingbot** is the most-used open-source market-making /
  arbitrage bot on CEXes and DEXes. Serious retail MMs run it and
  need persistent decision logs across strategy restarts.
- **NautilusTrader** is the serious-quant Rust-core / Python-API
  platform — event-driven, high-frequency, gaining ground in crypto
  quant shops. A `BrainctlActor` base class slots cleanly into its
  Actor model.
- **OctoBot** has a first-class tentacle plugin system and the biggest
  install count of any open-source retail crypto bot outside
  Hummingbot. A `brainctl_service` tentacle lets thousands of users
  opt in with one click.

---

## Priority 3 — general agent frameworks

Roadmap-only. No placeholder directories yet — these are
lower-launch-leverage for a crypto-themed launch but we'll want them
before a general-purpose 2.0 release.

| Framework | Language | Likely pattern |
|---|---|---|
| [LangGraph](https://langchain-ai.github.io/langgraph/) | Py + TS | Tool wrapper + checkpoint store |
| [CrewAI](https://www.crewai.com/) | Py | Tool wrapper + crew memory backend |
| [AutoGen](https://microsoft.github.io/autogen/) | Py | Tool wrapper |
| [Pydantic AI](https://ai.pydantic.dev/) | Py | Tool wrapper |
| [Mastra](https://mastra.ai/) | TS | Tool + memory provider |
| [Goose](https://block.github.io/goose/) | Rust | MCP installer |

---

## Deliberately skipped

- **Devin** (Cognition / Coinbase-hosted) — cloud-only with no local
  install surface. No MCP, no hook runtime, no filesystem we can
  inject into. A "knowledge export" script was considered and dropped
  because it's a one-shot dump, not a round-trippable integration.
- **Bare exchange SDKs** (ccxt, python-binance, hyperliquid-python) —
  wrong layer. brainctl integrates with *agents* that use exchanges,
  not the exchange SDKs themselves.
- **Unmaintained bots** (Gekko, Zenbot, Tribeca) — dead projects.
- **Closed-source desks** (Tradingview Pine, Quantconnect LEAN cloud,
  proprietary MM stacks) — no contribution surface.

---

## How new plugins get added

1. **Pick a pattern.** Mixin, MCP installer, or hook/skill — don't
   invent a fourth.
2. **Copy the closest existing plugin** as a template:
   - Mixin -> `plugins/freqtrade/brainctl/` or
     `plugins/jesse/brainctl/`
   - MCP installer -> `plugins/codex/brainctl/` or
     `plugins/cursor/brainctl/`
   - Hook/skill -> `plugins/claude-code/brainctl/` or
     `plugins/openclaw/brainctl/`
3. **Ship the four required files** at minimum: `plugin.yaml`,
   `install.py` (or equivalent), one template file, `README.md`.
4. **Bump version.** Placeholders use `0.0.0-placeholder`. Shipped
   plugins start at `0.1.0` and follow semver.
5. **Update this doc.** Move the plugin's row from "placeholder" to
   the appropriate "shipped" table.
6. **PR.** One plugin per PR; core brainctl changes go in a separate
   PR.

---

## Versioning

- `0.0.0-placeholder` — directory exists, `plugin.yaml` has
  `status: placeholder`, README explains the planned shape, no
  executable code.
- `0.1.0` — first functional implementation.
- `0.2.0+` — feature adds or breaking changes, follows semver.

The `status` field in `plugin.yaml` is the source of truth for
placeholder vs shipped — tooling can grep for `status: placeholder` to
hide incomplete plugins from install listings.
