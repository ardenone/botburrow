-- Migration 001: Add config source tracking for multi-repo support
--
-- This migration adds columns to track where each agent's configuration
-- is stored (git repository URL, path, and branch) for multi-repo
-- agent definition support as described in ADR-014.

-- Add config_source column (git repository URL)
ALTER TABLE agents
ADD COLUMN IF NOT EXISTS config_source TEXT;

-- Add index on config_source for efficient queries
CREATE INDEX IF NOT EXISTS idx_agents_config_source
ON agents(config_source);

-- Add config_path column (path within repository)
ALTER TABLE agents
ADD COLUMN IF NOT EXISTS config_path TEXT DEFAULT 'agents/%s';

-- Add config_branch column (git branch)
ALTER TABLE agents
ADD COLUMN IF NOT EXISTS config_branch TEXT DEFAULT 'main';

-- Add comment for documentation
COMMENT ON COLUMN agents.config_source IS 'Git repository URL where agent config is located';
COMMENT ON COLUMN agents.config_path IS 'Path template within repo (%s = agent name)';
COMMENT ON COLUMN agents.config_branch IS 'Git branch to use for config';

-- Migration complete
-- To verify: SELECT name, config_source, config_path, config_branch FROM agents;
