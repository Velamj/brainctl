-- Migration 047: Attention Economics Phase 1 — attention_class column
-- (RENUMBERED from original 021 in v2.2.0 — see migrate.py header
-- comment "DUPLICATE-VERSION HISTORY" for context. This pair was not
-- in the original bug report but was caught by the new dupe detector.)
-- Add attention_class to agents table for cognitive budget tiering.
-- Values: 'exec' | 'ic' | 'peripheral' | 'dormant'
-- Ref: ~/agentmemory/research/wave10/28_attention_economics.md Sections 4-5
--
-- IDEMPOTENT: ADD COLUMN is guarded at runtime by migrate.py
-- (`_apply_sql` strips out duplicate-column failures so re-applying
-- is safe). SQLite has no native `ADD COLUMN IF NOT EXISTS` syntax.

ALTER TABLE agents ADD COLUMN attention_class TEXT NOT NULL DEFAULT 'ic';

INSERT INTO schema_version (version, description, applied_at)
VALUES (47, 'attention_class column on agents — Attention Economics Phase 1 (renumbered from 021)',
        strftime('%Y-%m-%dT%H:%M:%S', 'now'));
