-- Migration 002: Add API key expiration column
--
-- This migration adds the api_key_expires_at column to the agents table
-- to support scheduled API key rotation. This column is nullable and indexed
-- for efficient querying of expiring keys (required for bd-pd2).

-- Add api_key_expires_at column (nullable, with index)
ALTER TABLE agents
ADD COLUMN IF NOT EXISTS api_key_expires_at TIMESTAMPTZ;

-- Add index on api_key_expires_at for efficient queries of expiring keys
CREATE INDEX IF NOT EXISTS idx_agents_api_key_expires_at
ON agents(api_key_expires_at);

-- Add comment for documentation
COMMENT ON COLUMN agents.api_key_expires_at IS 'API key expiration timestamp for scheduled rotation';

-- Migration complete
-- To verify: SELECT name, api_key_expires_at FROM agents;
