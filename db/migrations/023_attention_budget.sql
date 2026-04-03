-- Migration 023: Attention Budget System — tiered token budgets + access_log token accounting
-- : Agent-class profiles (4 tiers), tokens_consumed in access_log, fleet spend tracking
-- Ref: ~/agentmemory/research/wave10/28_attention_economics.md

-- ── 1. attention_budget_tier column on agents ───────────────────────────────
-- Tier 0 = CEO/orchestrators  → unlimited
-- Tier 1 = senior IC agents   → 5000 tokens/heartbeat default
-- Tier 2 = specialist agents  → 2000 tokens/heartbeat default
-- Tier 3 = worker agents      →  500 tokens/heartbeat default
-- Default for existing agents: Tier 1 (safe downgrade from current unlimited)

ALTER TABLE agents ADD COLUMN attention_budget_tier INTEGER NOT NULL DEFAULT 1;

-- Promote exec-class agents (already tagged via 021_attention_class.sql) to Tier 0
UPDATE agents SET attention_budget_tier = 0 WHERE attention_class = 'exec';

-- peripheral → Tier 2, dormant → Tier 3
UPDATE agents SET attention_budget_tier = 2 WHERE attention_class = 'peripheral';
UPDATE agents SET attention_budget_tier = 3 WHERE attention_class = 'dormant';

-- ── 2. tokens_consumed column on access_log ─────────────────────────────────
-- Estimated via response length heuristic: 1 token ≈ 4 chars

ALTER TABLE access_log ADD COLUMN tokens_consumed INTEGER;

-- ── 3. Index for budget status queries ──────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_access_agent_day
    ON access_log(agent_id, created_at DESC);

-- ── 4. Schema version ────────────────────────────────────────────────────────
INSERT OR IGNORE INTO schema_version (version, description, applied_at)
VALUES (23, 'attention_budget_tier on agents + tokens_consumed on access_log ', strftime('%Y-%m-%dT%H:%M:%S', 'now'));
