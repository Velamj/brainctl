# Changelog

All notable changes to **brainctl** will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [1.5.0] — 2026-04-13

### Safe upgrade path for existing brain.db files

Closes the sharp edge where users upgrading brainctl could silently break
writes against older `brain.db` schemas. Adds the diagnostic and backfill
tools needed to safely align a pre-existing `brain.db` with a newer version
of the package.

### Added
- **`brainctl migrate --status-verbose`** — per-migration DDL heuristic. Each
  migration file gets classified as `likely-applied`, `partial`, `pending`,
  or `unknown` based on whether its expected columns/tables already exist
  in the schema. Works by regex-parsing `ALTER TABLE ADD COLUMN` and
  `CREATE TABLE` statements and checking `sqlite_master` / `PRAGMA
  table_info`. Imperfect but genuinely diagnostic — revealed real partial-
  apply drift in a test database that ad-hoc inspection had missed.
- **`brainctl migrate --mark-applied-up-to N`** — backfill `schema_versions`
  for migrations 1..N as "already applied" without running their SQL. For
  `brain.db` files that predate the migration tracking framework: their
  schema already has the effects, but the tracker is virgin, so
  `brainctl migrate` would try to re-apply everything and crash on column
  collisions. Rows written with a `(backfilled)` name suffix so they're
  distinguishable from "really ran" tracking rows.
  - **Guard**: refuses to go below the current high-water mark (prevents
    rewriting tracker state you've already committed to). Backfilling
    *above* the high-water mark is always allowed — this handles the
    partial-tracker case where a user ran `brainctl migrate`, got a few
    through, crashed, and now needs to skip the rest.
  - Supports `--dry-run` for preview.
  - Duplicate-version migrations (`012_*`, `013_*`, `017_*`, `021_*`,
    `023_*` each have two files) collapse into one `schema_versions` row.
- **`Brain()` pending-migrations warning** — `Brain(db_path)` against an
  existing `brain.db` with pending migrations emits a `logging.warning` with
  branched advice:
  - **Virgin tracker** (`applied == 0 AND pending > 0`): "migration tracking
    not initialized — run `brainctl doctor` for diagnosis." **Never** tells
    the user to run `brainctl migrate` blindly — that would crash on
    pre-existing columns.
  - **Partial tracker** (`applied > 0 AND pending > 0`): "N pending, run
    `brainctl migrate`."
  - Deduped per-process per-db via a module-level flag so multiple `Brain()`
    constructions don't spam the log.
  - Gated on `BRAINCTL_SILENT_MIGRATIONS=1` for CI and tests.
- **`brainctl doctor` migrations section** — three-state diagnostic matching
  the Brain warning, plus a fourth state `virgin-tracker-with-drift`
  triggered when the tracker is empty but the schema shows ≥2 late-migration
  marker columns (`write_tier`, `ewc_importance`, `labile_until`,
  `memory_type`, `protected`). In that state, doctor prints the full
  4-step recovery workflow:
  1. `brainctl migrate --status-verbose`
  2. apply truly-pending migrations manually via `sqlite3`
  3. `brainctl migrate --mark-applied-up-to N`
  4. `brainctl migrate`
- **README `Upgrading` section** — covers the normal upgrade path, the
  virgin-tracker-with-drift edge case, the full recovery workflow,
  backup/rollback guidance. Visible right after `Install` so new users
  encounter it before they pip-upgrade an existing brain.

### Tests
- 21 new test cases in `tests/test_migrate.py`:
  - `TestMarkAppliedUpTo` (11): dry-run, real backfill, idempotent re-run,
    extend above high-water, guard refuses below high-water / above max /
    N<1, partial tracker extend, duplicate-version collapse, subsequent
    `migrate run` correctly skips backfilled versions.
  - `TestStatusVerbose` (5): extends base status, classifies every
    migration, fresh db shows `likely-applied`, bare db shows `pending`,
    UPDATE-only migrations land in `unknown`.
  - `TestBrainMigrationWarning` (5): virgin → doctor advice (never
    migrate), partial → migrate advice, up-to-date is silent, env var
    suppression, dedupe across multiple constructions.
  - `TestDoctorMigrationsCheck` (2): JSON includes migrations section,
    detects virgin-tracker-with-drift via late-column markers.
- Full suite: 1369 passing, no regressions.

### Known limitations
- The migration runner itself is not yet idempotent at the statement
  level — collisions on `ALTER TABLE ADD COLUMN` still crash the run
  (stops on first error). The v1.5.0 story is "detect before applying";
  a DDL-only idempotent runner with savepoints is planned for v1.5.1.
- `status()` returns `total` (file count) and `applied`/`pending` (version
  counts), so `total != applied + pending` when duplicate-version
  migrations are present. Cosmetic; will be cleaned up in v1.5.1.

## [1.4.0] — 2026-04-13

### Added
- **Codex CLI plugin** — `plugins/codex/brainctl/` gives [OpenAI Codex CLI](https://github.com/openai/codex)
  persistent memory via the brainctl MCP server. Ships:
  - `install.py` — idempotent, sentinel-wrapped merge of `[mcp_servers.brainctl]`
    into `~/.codex/config.toml`. Supports `--dry`, `--print`, `--uninstall`
    with automatic backup. Leaves other MCP servers and config untouched.
  - `AGENTS.md.template` — session-lifecycle instructions (orient on start,
    wrap up on end) that users drop into their project's `AGENTS.md` so Codex
    auto-loads the brainctl memory protocol on every session.
  - `README.md` — install + usage + troubleshooting.
  - `plugin.yaml` — metadata with `brainctl[mcp]>=1.3.0` as the pip floor
    (MCP extra required for the `brainctl-mcp` server entry point).
  Once installed, Codex gets the full brainctl tool surface (196 tools)
  including the native `agent_orient` / `agent_wrap_up` session bookends
  shipped in v1.3.0.

## [1.3.0] — 2026-04-13

### Added
- **Eliza plugin** — `plugins/eliza/brainctl/` ships `@brainctl/eliza-plugin`, a
  TypeScript plugin that gives Eliza agents persistent memory via the brainctl
  MCP server. Six actions (`BRAINCTL_REMEMBER` / `SEARCH` / `ORIENT` / `WRAP_UP`
  / `DECIDE` / `LOG`) plus an auto-recall memory provider that injects context
  before every message. Spawns `brainctl-mcp` as a stdio subprocess via
  `@modelcontextprotocol/sdk`. (#67)
- **Claude Code plugin** — `plugins/claude-code/brainctl/` hooks into Claude
  Code's `session_start`, `session_end`, `user_prompt_submit`, and
  `post_tool_use` events. Adds `brainctl orient` / `brainctl wrap_up` CLI
  commands for manual bookends and `<private>` redaction for sensitive
  content in memories.
- **Freqtrade plugin** — `plugins/freqtrade/brainctl/` — strategy mixin that
  gives Freqtrade strategies persistent memory across backtests and live
  runs. (#68)
- **Jesse plugin** — `plugins/jesse/brainctl/` — strategy mixin for the
  [Jesse](https://jesse.trade) algotrading framework, same shape as the
  Freqtrade plugin. (#69)
- **Native `agent_orient` / `agent_wrap_up` MCP tools** — session-lifecycle
  primitives are now first-class MCP tools instead of being composed
  client-side from `handoff_latest` / `handoff_add` / `event_search`. Plugins
  that want session bookends can call them directly. Tool count: 196. (#70)

### Changed
- **Lazy shared sqlite3 connection per `Brain` instance** — opens a single
  shared connection on first use instead of churning a new one per operation.
  (#62)
- **Schema rebase** — `init_schema.sql` rebased, 6 dead tables dropped, archive
  safety net added so historical rows survive schema migrations. (#63)
- **MCP helper consolidation** — duplicate helpers across MCP tool modules
  consolidated into `agentmemory.lib.mcp_helpers`. No behavior change. (#64)

### Docs
- Hermes plugin install path clarified: user-plugin dir is
  `~/.hermes/plugins/brainctl`, and any Python deps must be installed into
  Hermes's venv (not the shell's default pip). Workaround for the Hermes
  memory-provider discovery mismatch documented. (See also:
  [NousResearch/hermes-agent#9246](https://github.com/NousResearch/hermes-agent/pull/9246)
  which lands brainctl in-tree and removes the workaround entirely.)

## [1.2.0] — 2026-04-13

### Added
- **Hermes Agent memory provider plugin** — `plugins/hermes/brainctl/` ships a
  drop-in `MemoryProvider` implementation for [Hermes Agent](https://hermes-agent.nousresearch.com).
  Wraps `agentmemory.Brain`, exposes `brainctl_remember` / `search` / `think` /
  `log` / `entity` / `decide` / `handoff` tools to the model, auto-prefetches
  recall before each turn, auto-retains completed turns, runs `brain.orient()`
  /`brain.wrap_up()` session bookends, mirrors built-in `MEMORY.md` writes into
  `brain.db`, and persists pre-compression context as `lesson` memories.
- **Context profiles** — task-scoped search presets via `--profile NAME` on `search` and `memory search`
  - 6 built-in profiles: `writing`, `meeting`, `research`, `ops`, `networking`, `review`
  - Each profile scopes tables + categories to what's relevant for that task mode (inspired by Koylan's progressive disclosure pattern)
  - User-defined profiles stored in brain.db: `brainctl profile create/list/show/delete`
  - MCP: `profile` param on `memory_search` and `search` tools — `{"tool":"memory_search","query":"voice","profile":"writing"}`
  - Explicit `--tables` / `--category` flags always win over profile defaults
- **`brainctl obsidian`** — bidirectional sync with Obsidian vaults
  - `export <vault>` — dumps active memories, entities, and events to markdown with YAML frontmatter; follows Karpathy LLM-wiki 3-layer pattern
  - `import <vault>` — ingests new vault notes (no `brainctl_id`) through `Brain.remember()` / W(m) gate; `--dry-run` supported
  - `watch <vault>` — watchdog-based live ingest on create/modify; configurable `--cooldown` window (requires `pip install watchdog`)
  - `status <vault>` — diff table of brain.db vs vault counts, flags un-exported drift
- **Replay priority & SWR tagging** — `replay_priority` and `ripple_tags` columns accumulate dynamically on vsearch retrievals (salience = score × confidence)
- **Reconsolidation window** — `labile_until`, `labile_agent_id`, `retrieval_prediction_error` columns; 20-min lability window opened on high-PE retrieval; agent-scoped write access
- **Arousal-precision coupling** (Free Energy Principle) — `arousal_gain` multiplier in W(m) gate; high-arousal content consolidates stronger
- **5 new MCP consolidation tools** — `replay_boost`, `replay_queue`, `reconsolidation_check`, `reconsolidate`, `consolidation_stats` (176 MCP tools total)

## [1.0.0] — 2026-04-03

Stable release. Every feature verified end-to-end on clean pip install.

### Highlights
- **102 tests** passing in CI (Python 3.11-3.13)
- **81-table production schema** via `brainctl init`
- **44-emotion affect tracking** with 6 safety patterns
- **11-pass consolidation engine** (decay, merge, dream, Hebbian learning)
- **3D neural map** with brain-region layout and live activity feed
- **23-tool MCP server** for Claude Desktop / VS Code
- **Zero-LLM-cost** search, classify, consolidate — all local computation
- **`brainctl report`** compiles knowledge into readable markdown
- **`brainctl lint`** health checks with auto-fix
- Clean JSON error handling on all commands
- Full documentation: README, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY

### Added since 0.5.0
- CI fix: test fixtures use `brainctl init` (no production DB dependency)
- Neural Map v6: removed empty bubbles, color-coded edges, legend panel
- Orphan agents hidden, connected agents positioned by content
- `/api/activity` endpoint for live visualization feed
- Crypto team redirected: cancelled over-engineered Solana contract work
- `brainctl-consolidate` entry point for pip install

## [0.3.0] — 2026-04-03

### Added
- **`brainctl init`** — create fresh brain.db with full production schema (30+ tables)
- **MCP server in package** — `brainctl-mcp` now works from pip install (was broken in 0.2.0)
- **Affect MCP tools** — `affect_classify`, `affect_log`, `affect_check`, `affect_monitor` (16 total MCP tools)
- **Write gate integration** — arousal-modulated memory worthiness (high-arousal memories consolidate 40% stronger)
- **brain.py API** — `Brain.affect(text)` and `Brain.affect_log(text)` with type hints
- `affect_log` table in init_schema.sql

### Fixed
- `brainctl-mcp` crash on pip install (missing module, now at `agentmemory.mcp_server`)
- `brainctl init` now uses full schema, not toy 7-table schema
- README: `from brainctl import Brain` corrected to `from agentmemory import Brain`
- Dominance scoring now respects negation ("can't fix" = low dominance, not high)
- Added word forms to lexicons (panicking, terrifying, overwhelmed, etc.)

### Changed
- Schema files unified (db/ and src/agentmemory/db/ now in sync)
- Version numbers aligned across all files

## [0.2.0] — 2026-04-03

### Added
- **Functional affect tracking system** grounded in Anthropic's "Emotion Concepts in LLMs" (2026)
- `src/agentmemory/affect.py` — zero-LLM-cost lexical affect classifier (~1ms)
- 44 named emotions with validated PAD coordinates (valence/arousal/dominance)
- 11 affect clusters matching Anthropic paper findings
- 6 safety patterns detecting manipulation, coercion, sycophancy, deception risks
- Arousal-modulated write gate boost and consolidation priority scoring
- Affect distance metric and velocity tracking
- Fleet-wide affect monitoring for 200 agents
- CLI: `brainctl affect classify|log|check|history|monitor`
- 35 affect-specific tests

## [0.1.1] — 2026-04-03

### Added
- `brainctl cost` — token consumption dashboard with format savings analysis
- `--output json|compact|oneline` on search commands (97% token savings with oneline)
- `--budget` flag for hard token caps on search output
- 50 pytest tests (Brain API + CLI + output formats)
- Dockerfile (python:3.12-slim, MCP server default)
- GitHub Actions CI (Python 3.11-3.13) + PyPI trusted publish on tag
- CONTRIBUTING.md
- Web UI: token cost cards in health view, `/api/cost` endpoint
- 19 new entities + 25 edges seeded into knowledge graph

### Fixed
- MCP_SERVER.md install docs (`agentmemory` → `brainctl[mcp]`)
- Decisions renderer in web UI (handles `title` field)

## [0.1.0] — 2026-04-03

### Added
- Initial PyPI release as `brainctl`
- `Brain` class Python API: remember, search, forget, log, entity, relate, decide, stats
- CLI with 40+ commands across memory, entity, event, search, trigger, neuro subsystems
- MCP server with 12 tools for Claude Desktop / VS Code
- FTS5 full-text search + optional sqlite-vec vector search
- Bayesian confidence with alpha/beta parameters
- Write gate with surprise scoring
- Neuromodulation system (dopamine, acetylcholine, norepinephrine, serotonin)
- Knowledge graph with typed entities and directed relations
- Consolidation engine (confidence decay, dream synthesis, Hebbian learning)
- Prospective memory triggers
- Multi-agent support with per-agent attribution
- Web dashboard on port 3939
