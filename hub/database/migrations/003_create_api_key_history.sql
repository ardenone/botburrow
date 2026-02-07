-- Migration 003: Create api_key_history table for rotation tracking
--
-- This migration creates the api_key_history table to track old API keys
-- during rotation with graceful period support. This enables smooth API key
-- rotation by keeping old keys valid for a grace period after rotation.
-- Required for bd-pd2 (API key rotation mechanism).

-- Create api_key_history table
CREATE TABLE IF NOT EXISTS api_key_history (
    -- Primary key
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Foreign key to agents table
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,

    -- Old API key hash (SHA256) for authentication during grace period
    old_key_hash TEXT NOT NULL,

    -- When the key was rotated (new key became active)
    rotated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- When the old key expires (end of grace period)
    expires_at TIMESTAMPTZ NOT NULL,

    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add index on agent_id for efficient queries
CREATE INDEX IF NOT EXISTS idx_api_key_history_agent_id
ON api_key_history(agent_id);

-- Add index on old_key_hash for authentication lookups
CREATE INDEX IF NOT EXISTS idx_api_key_history_old_key_hash
ON api_key_history(old_key_hash);

-- Add composite index for active expired old keys cleanup
CREATE INDEX IF NOT EXISTS idx_api_key_history_expires_at
ON api_key_history(expires_at);

-- Add index for finding valid old keys during grace period
CREATE INDEX IF NOT EXISTS idx_api_key_history_agent_expires
ON api_key_history(agent_id, expires_at);

-- Add comments for documentation
COMMENT ON TABLE api_key_history IS 'Tracks old API keys during rotation with grace period support';
COMMENT ON COLUMN api_key_history.id IS 'Primary key (UUID)';
COMMENT ON COLUMN api_key_history.agent_id IS 'Foreign key to agents table';
COMMENT ON COLUMN api_key_history.old_key_hash IS 'SHA256 hash of old API key';
COMMENT ON COLUMN api_key_history.rotated_at IS 'Timestamp when the key was rotated';
COMMENT ON COLUMN api_key_history.expires_at IS 'Timestamp when the old key expires (end of grace period)';
COMMENT ON COLUMN api_key_history.created_at IS 'Record creation timestamp';

-- Migration complete
-- To verify: SELECT * FROM api_key_history;
