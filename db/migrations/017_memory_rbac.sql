-- Migration 017: Memory RBAC — visibility column + read_acl / --
-- Adds two-layer access control to memories:
--   visibility: 'public' | 'project' | 'agent' | 'restricted'  (DEFAULT public)
--   read_acl:   JSON array of agent_ids allowed to read (nullable, for 'agent'/'restricted')
--
-- Non-destructive: existing memories default to 'public'.

ALTER TABLE memories ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public';
ALTER TABLE memories ADD COLUMN read_acl TEXT;  -- nullable JSON array e.g. '["agent-a","agent-b"]'

-- Validation trigger: reject invalid visibility values on INSERT
CREATE TRIGGER memories_visibility_check_insert
BEFORE INSERT ON memories
WHEN NEW.visibility NOT IN ('public', 'project', 'agent', 'restricted')
BEGIN
    SELECT RAISE(ABORT, 'memories.visibility must be one of: public, project, agent, restricted');
END;

-- Validation trigger: reject invalid visibility values on UPDATE
CREATE TRIGGER memories_visibility_check_update
BEFORE UPDATE OF visibility ON memories
WHEN NEW.visibility NOT IN ('public', 'project', 'agent', 'restricted')
BEGIN
    SELECT RAISE(ABORT, 'memories.visibility must be one of: public, project, agent, restricted');
END;

-- Index for fast visibility filtering
CREATE INDEX idx_memories_visibility ON memories(visibility);

-- Backfill: ensure all existing memories are public (should already be default, but be explicit)
UPDATE memories SET visibility = 'public' WHERE visibility IS NULL OR visibility = '';
