# Changelog

All notable changes to **brainctl** will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [1.6.0] — 2026-04-15

This release lands four bodies of work: new retrieval-quality
instrumentation, a first-class entity synthesis surface, self-healing
gap scans, and the plugin set brainctl launches with (cursor, openclaw,
trading-bot and crypto-native agent-framework placeholders). It also
includes a broad code-quality sweep across the core modules.

### Added — plugins
- **Cursor plugin** (`plugins/cursor/brainctl/`). Idempotent installer
  that wires the brainctl MCP server into `~/.cursor/mcp.json` with a
  sentinel-wrapped block, ships a `.mdc` rules template that teaches
  Cursor the orient / wrap_up lifecycle on every session start, and
  supports `install.py --dry` / `--print` / `--uninstall` with
  automatic backups. Same ergonomic shape as the Codex CLI plugin
  from v1.4.0.
- **OpenClaw plugin** (`plugins/openclaw/brainctl/`). Ships as a skill
  + `AGENTS.md` snippet injection rather than a config-file merge —
  OpenClaw's multi-agent topology discovers brainctl through its
  skill registry rather than a static config file. This closes the
  one plugin that the brainctl launch site has been listing as
  "coming next".
- **Trading-bot plugin placeholders** under `plugins/` for
  `hummingbot`, `nautilustrader`, `octobot`, and `coinbase-agentkit`.
  Each is a scaffold with a `README.md`, install stub, and the
  integration points brainctl needs — enough structure that a
  contributor can land a working integration without architectural
  uncertainty. Roadmap lives at `plugins/TRADING_INTEGRATIONS.md`.
- **Crypto-native agent-framework plugin placeholders** for `rig`,
  `virtuals-game`, and `zerebro`. Same scaffold-and-ready structure
  as the trading bots; these target the Solana / crypto-agent
  ecosystem rather than traditional trading desks.

### Added — retrieval quality instrumentation
- **Deterministic search-quality benchmark harness.** `tests/bench/`
  seeds a 29-memory / 8-event / 6-entity corpus with 20 graded queries
  (entity / procedural / decision / temporal / troubleshooting / negative /
  ambiguous), runs them through either `Brain.search` or the full
  `cmd_search` hybrid pipeline, and reports P@1 / P@5 / Recall@5 / MRR /
  nDCG@5. Committed baseline at `tests/bench/baselines/search_quality.json`;
  pytest regression gate in `tests/test_search_quality_bench.py` fails on
  any >2% drop on a headline metric. `bin/brainctl-bench` is the
  first-class CLI wrapper.
- **Query-intent taxonomy normalization in `cmd_search`.** The regex
  classifier in `bin/intent_classifier.py` produces 10 labels; downstream
  rerank branches only checked 6. Added `_INTENT_ALIAS` mapping so every
  classifier output reaches a concrete rerank profile
  (`historical_timeline`/`task_status`/`troubleshooting`/`orientation` →
  `event_lookup`, `decision_rationale` → `decision_lookup`, `how_to`/
  `research_concept` → `procedural`, `cross_reference` → `entity_lookup`,
  `factual_lookup` → `general`). Effective rerank branch is surfaced as
  `metacognition.rerank_branch` on every search response.

### Added — entity synthesis surface
- **Migration 033 — `entities.compiled_truth`.** Adds a rewriteable
  "current best understanding" block to each entity, populated from
  observations + linked memories + linked events. CLI:
  `brainctl entity compile [--all]`, `brainctl entity get ID --compiled`.
  MCP tool: `entity_compile`.
- **Migration 034 — `entities.enrichment_tier`.** Auto-classifies entities
  into Tier 1 (critical) / Tier 2 (notable) / Tier 3 (minor) from recall
  count, knowledge-edge degree, and event-link count. CLI: `brainctl entity
  tier [--refresh]`. MCP tool: `entity_tier`.
- **Migration 035 — `entities.aliases`.** First-class canonical-name list
  used by the merger as a pre-check before semantic dedup. CLI:
  `brainctl entity alias (list|add|remove) ID [values...]`. Helper
  `find_entity_by_alias()` for the merger. MCP tool: `entity_alias`.
- `bin/consolidation-cycle.sh` now runs `entity tier --refresh` and
  `entity compile --all` after the gap scan.

### Added — self-healing gap scans
- **Migration 036 — `knowledge_gaps` CHECK expansion.** Adds three
  gap types: `orphan_memory` (no edges + no recalls + old),
  `broken_edge` (`knowledge_edges` pointing at deleted rows),
  `unreferenced_entity` (no edges, no observations, old). Rebuilt via
  temp-table round-trip so the schema stays byte-identical to a fresh
  install. `brainctl gaps scan` now reports all three (with
  `--skip-self-healing` for fast mode).
- Latent schema bug fixed along the way: the `recent_belief_collapses`
  view referenced a non-existent `belief_collapse_events_old` table,
  which would have crashed any future DDL touching the schema. Rebuilt
  to point at the real `belief_collapse_events` table in init_schema.sql
  and migration 036.

### Fixed — natural-language query regressions in `cmd_search`
- `_FTS5_SPECIAL` regex was missing `?`, `!`, `'`, `` ` ``, `,`, `;`, `:`,
  causing `fts5: syntax error near "?"` on common natural-language
  queries. Extended the character class.
- `cmd_search` passed the space-separated sanitized query directly to
  FTS5 MATCH, which FTS5 treats as implicit AND. Natural-language
  queries ("What does Alice prefer?") silently returned zero results
  because FTS5 demanded every token match. Added
  `_build_fts_match_expression` that drops stopwords and joins meaningful
  tokens with `OR`, restoring parity with `Brain.search` behavior.

### Changed — code quality sweep
- **Deduplicated `DB_PATH` boilerplate** and the `_days_since` helper
  across modules into a single shared source of truth. Reduces drift
  risk on path resolution and date math.
- **Dead code elimination pass.** Removed unused functions, imports,
  and branches surfaced by a coverage pass — roughly the set of code
  that was never executed by the test suite or the CLI / MCP entry
  points.
- **Weak type annotations strengthened.** Local variables that were
  previously `Any` or untyped now carry precise types, which both
  improves pyright/mypy output and catches a handful of latent
  argument-order bugs at type-check time.
- **Defensive `try/except` blocks that hid bugs have been removed.**
  The rule applied: a `try/except` that catches a broad exception
  and logs a warning is only acceptable at a genuine trust boundary
  (user input, network calls, filesystem). Internal paths that had
  been wrapped in `except Exception: pass` or `except: log.warning(...)`
  are now bare — if an invariant breaks, we want the traceback, not
  a silent swallow.
- **Deprecated / legacy code and dead branches removed.** A cleanup
  of code paths left behind from earlier migrations, feature flags
  that never got flipped, and compatibility shims for interfaces no
  caller uses anymore.
- **AI-slop narration comments removed.** Comments that described
  what the next line was doing in English prose — the kind that
  appear when an LLM writes code and explains its work — have been
  purged. Remaining comments are either WHY-level or WORKAROUND
  markers.

## [1.5.2] — 2026-04-14

### Fixed
- **`brainctl version` reported the wrong version.** `src/agentmemory/_impl.py`
  hard-coded `VERSION = "1.1.2"` — a separate string from `__version__` in
  `agentmemory/__init__.py` — that nobody had been bumping across releases.
  Users on v1.2.0 through v1.5.1 ran `brainctl version` and saw `"version":
  "1.1.2"`. No functional impact (pyproject metadata, pip install, and the
  Python API all read the correct `__version__`), but misleading and likely
  caused at least one user to think they were running a much older build
  than they actually had installed.
  Fix: `_impl.py` now imports `VERSION` directly from
  `agentmemory.__version__`, so the CLI version string is pinned to the
  same source of truth as pip metadata. Can't drift again.

## [1.5.1] — 2026-04-13

### Fixed — `status_verbose` heuristic false negatives

Both bugs were discovered live while walking a production `brain.db`
through the v1.5.0 recovery workflow. Neither corrupted data, but both
caused the diagnostic to mis-classify already-applied migrations as
`partial` or `pending`, which would have led a less-cautious user to
re-run migrations and clobber state.

- **Generated virtual columns were invisible.** `status_verbose` used
  `PRAGMA table_info` which hides columns added via
  `ALTER TABLE ... ADD COLUMN ... GENERATED ALWAYS AS (...) VIRTUAL`.
  Migration 024 (confidence_alpha/beta) classified as `pending (0/2)`
  on a db that already had both columns, and attempting to re-apply
  crashed with `duplicate column name`. Fix: switch to
  `PRAGMA table_xinfo` which reveals hidden and generated columns.
- **`ADD COLUMN IF NOT EXISTS` regex mis-capture.** The ADD COLUMN
  pattern was
  `ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)` — on migration 023's
  `ALTER TABLE access_log ADD COLUMN IF NOT EXISTS tokens_consumed`
  that captured `IF` as the column name and searched for a column
  named `IF` on `access_log`, producing a false `partial (7/8)`
  reading. Fix: tolerate an optional `IF NOT EXISTS` between `COLUMN`
  and the identifier:
  `ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)`.

### Added — regression tests
- `test_generated_virtual_columns_detected` — builds a fixture db with
  `GENERATED ALWAYS AS (...) VIRTUAL` columns and asserts migration 024
  classifies as `likely-applied (2/2)`.
- `test_add_column_if_not_exists_regex` — builds a fixture db with
  every column migration 023 expects and asserts the result is
  `likely-applied (8/8)`, not `partial (7/8)`.

Both tests would fail against v1.5.0's heuristic and pass against
v1.5.1's.

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
