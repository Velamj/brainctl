-- Migration 021: Attention Economics Phase 1 — attention_class column
-- : Add attention_class to agents table for cognitive budget tiering
-- Values: 'exec' | 'ic' | 'peripheral' | 'dormant'
-- Ref: ~/agentmemory/research/wave10/28_attention_economics.md Sections 4-5

ALTER TABLE agents ADD COLUMN attention_class TEXT NOT NULL DEFAULT 'ic';

INSERT OR IGNORE INTO schema_version (version, description, applied_at)
VALUES (21, 'attention_class column on agents (Attention Economics Phase 1, ', strftime('%Y-%m-%dT%H:%M:%S', 'now'));
