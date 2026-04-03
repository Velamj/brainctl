-- Migration 023: Add search instrumentation columns to agent_uncertainty_log -- Author: Engram (Memory Systems Lead)
-- Date: 2026-03-28
-- Purpose: Extend agent_uncertainty_log with passive search telemetry columns so that
--          brainctl search can append a row capturing per-query metrics.
--          These columns coexist with the pre-task gap-tracking columns added in migration 022
-- by Cortex). Gap-tracking rows fill gap_topic/free_energy; search rows fill
--          domain/query/result_count/avg_confidence.
-- References: , (Wave 10 Active Inference research)
-- Schema version: 22 -> 23

ALTER TABLE agent_uncertainty_log ADD COLUMN domain         TEXT;
ALTER TABLE agent_uncertainty_log ADD COLUMN query          TEXT;
ALTER TABLE agent_uncertainty_log ADD COLUMN result_count   INTEGER;
ALTER TABLE agent_uncertainty_log ADD COLUMN avg_confidence REAL;
ALTER TABLE agent_uncertainty_log ADD COLUMN retrieved_at   DATETIME DEFAULT (datetime('now'));
ALTER TABLE agent_uncertainty_log ADD COLUMN temporal_class TEXT     DEFAULT 'ephemeral';
ALTER TABLE agent_uncertainty_log ADD COLUMN ttl_days       INTEGER  DEFAULT 30;

CREATE INDEX IF NOT EXISTS idx_unc_domain     ON agent_uncertainty_log(domain);
CREATE INDEX IF NOT EXISTS idx_unc_retrieved  ON agent_uncertainty_log(retrieved_at);

INSERT OR REPLACE INTO schema_version (version, applied_at, description)
VALUES (23, datetime('now'),
    'agent_uncertainty_log — search instrumentation columns: domain, query, result_count, avg_confidence, retrieved_at, temporal_class, ttl_days ');

-- Also backfill missing tokens_consumed on access_log (pre-existing schema gap)
ALTER TABLE access_log ADD COLUMN IF NOT EXISTS tokens_consumed INTEGER;

PRAGMA user_version = 23;
