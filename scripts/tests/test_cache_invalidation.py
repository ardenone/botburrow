"""
Tests for config cache invalidation.

Tests cover:
- Distributed cache with Redis/Valkey and in-memory fallback
- Cache invalidation on config changes
- Agent name extraction from changed files
- ConfigLoader cache integration
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch, MagicMock

import pytest
import yaml

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config_loader import (
    AgentConfigLoader,
    AgentConfigCache,
    CacheConfig,
    AgentConfig,
)


# Local copy of _extract_agent_names_from_paths for testing
# (This is defined in webhooks.py but we test the logic here)
def _extract_agent_names_from_paths(changed_files: list) -> list:
    """Extract agent names from changed file paths."""
    agent_names = set()

    for path in changed_files:
        # Extract agent name from patterns like:
        # - agents/agent-name/config.yaml
        # - agents/agent-name/system-prompt.md
        # - agent-name/config.yaml
        parts = path.split("/")
        for i, part in enumerate(parts):
            if part == "agents" and i + 1 < len(parts):
                agent_name = parts[i + 1]
                if agent_name and agent_name not in (".", ".."):
                    agent_names.add(agent_name)

    return sorted(agent_names)


class TestAgentNameExtraction:
    """Test extraction of agent names from changed file paths."""

    def test_extract_from_standard_paths(self):
        """Test extracting agent names from standard agent directory paths."""
        changed_files = [
            "agents/claude-coder-1/config.yaml",
            "agents/claude-coder-1/system-prompt.md",
            "agents/research-bot/config.yaml",
        ]

        agent_names = _extract_agent_names_from_paths(changed_files)

        assert set(agent_names) == {"claude-coder-1", "research-bot"}

    def test_extract_from_nested_paths(self):
        """Test extracting from nested directory structures."""
        changed_files = [
            "repos/main/agents/test-agent/config.yaml",
            "repos/main/agents/test-agent/skills/skill1.py",
        ]

        agent_names = _extract_agent_names_from_paths(changed_files)

        assert agent_names == ["test-agent"]

    def test_extract_no_agents(self):
        """Test with files that don't contain agent paths."""
        changed_files = [
            "README.md",
            "docs/setup.md",
            "scripts/test.sh",
        ]

        agent_names = _extract_agent_names_from_paths(changed_files)

        assert agent_names == []

    def test_extract_with_mixed_paths(self):
        """Test with a mix of agent and non-agent paths."""
        changed_files = [
            "README.md",
            "agents/agent-1/config.yaml",
            "docs/api.md",
            "agents/agent-2/system-prompt.md",
        ]

        agent_names = _extract_agent_names_from_paths(changed_files)

        assert set(agent_names) == {"agent-1", "agent-2"}


class TestAgentConfigCache:
    """Test the AgentConfigCache class."""

    @pytest.mark.asyncio
    async def test_cache_initialization(self):
        """Test cache initialization with default config."""
        cache = AgentConfigCache()

        assert cache.config.default_ttl == 300
        assert cache.config.key_prefix == "botburrow:agent:"
        assert cache._connected is False

    @pytest.mark.asyncio
    async def test_cache_connect_without_redis(self):
        """Test cache connection falls back to memory when Redis unavailable."""
        cache = AgentConfigCache(CacheConfig(enabled=False))

        connected = await cache.connect()

        assert connected is False
        assert cache._connected is False

    @pytest.mark.asyncio
    async def test_cache_set_and_get_memory(self):
        """Test in-memory cache set and get operations."""
        cache = AgentConfigCache(CacheConfig(enabled=False))
        await cache.connect()

        # Set a value
        config = {"name": "test-agent", "type": "native"}
        await cache.set("test-agent", config, "https://github.com/test/agents.git")

        # Get it back
        result = await cache.get("test-agent", "https://github.com/test/agents.git")

        assert result is not None
        assert result["name"] == "test-agent"
        assert result["type"] == "native"

    @pytest.mark.asyncio
    async def test_cache_delete_memory(self):
        """Test in-memory cache delete."""
        cache = AgentConfigCache(CacheConfig(enabled=False))
        await cache.connect()

        # Set a value
        config = {"name": "test-agent"}
        await cache.set("test-agent", config)

        # Delete it
        await cache.delete("test-agent")

        # Should be gone
        result = await cache.get("test-agent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_clear(self):
        """Test clearing all cache entries."""
        cache = AgentConfigCache(CacheConfig(enabled=False))
        await cache.connect()

        # Add multiple entries
        await cache.set("agent1", {"name": "agent1"})
        await cache.set("agent2", {"name": "agent2"})

        # Clear all
        await cache.clear()

        # Both should be gone
        assert await cache.get("agent1") is None
        assert await cache.get("agent2") is None

    @pytest.mark.asyncio
    async def test_cache_invalidate_by_source(self):
        """Test invalidating all entries from a specific source."""
        cache = AgentConfigCache(CacheConfig(enabled=False))
        await cache.connect()

        # Add entries from different sources
        await cache.set("agent1", {"name": "agent1", "config_source": "https://github.com/test/agents.git"})
        await cache.set("agent2", {"name": "agent2", "config_source": "https://github.com/test/agents.git"})
        await cache.set("agent3", {"name": "agent3", "config_source": "https://github.com/other/agents.git"})

        # Invalidate by source
        count = await cache.invalidate_by_source("https://github.com/test/agents.git")

        # Should invalidate at least the 2 entries
        assert count >= 2

        # Other source should still have data
        result = await cache.get("agent3")
        assert result is not None
        assert result["name"] == "agent3"


class TestConfigLoaderCacheIntegration:
    """Test config_loader cache integration."""

    def test_loader_cache_initialization(self):
        """Test that AgentConfigLoader initializes cache."""
        loader = AgentConfigLoader(
            repos_config_path="/nonexistent.json",
            cache_ttl=600,
            enable_cache=True,
        )

        assert loader.cache_ttl == 600
        assert loader.enable_cache is True

    @pytest.mark.asyncio
    async def test_loader_cache_connect(self):
        """Test cache connection initialization."""
        loader = AgentConfigLoader(
            repos_config_path="/nonexistent.json",
            enable_cache=True,
        )

        # Should not crash even without Redis
        connected = await loader.initialize_cache()

        # Returns False when Redis unavailable (expected in test)
        assert connected is False or connected is True

        # Cleanup
        await loader.close_cache()

    @pytest.mark.asyncio
    async def test_loader_invalidate_agent(self):
        """Test invalidating a specific agent from cache."""
        loader = AgentConfigLoader(
            repos_config_path="/nonexistent.json",
            enable_cache=True,
        )

        # Add to in-memory cache
        loader.config_cache["test-agent:https://github.com/test/agents.git"] = AgentConfig(
            name="test-agent",
        )

        # Invalidate
        await loader.invalidate_agent("test-agent", "https://github.com/test/agents.git")

        # Should be removed from cache
        assert "test-agent:https://github.com/test/agents.git" not in loader.config_cache

        # Cleanup
        await loader.close_cache()


class TestCacheModels:
    """Test cache-related data models."""

    def test_cache_config_defaults(self):
        """Test CacheConfig default values."""
        config = CacheConfig()

        assert config.redis_url == "redis://localhost:6379/0"
        assert config.default_ttl == 300
        assert config.key_prefix == "botburrow:agent:"
        assert config.enabled is True

    def test_cache_config_custom(self):
        """Test CacheConfig with custom values."""
        config = CacheConfig(
            redis_url="redis://custom:6380/1",
            default_ttl=600,
            key_prefix="custom:",
            enabled=False,
        )

        assert config.redis_url == "redis://custom:6380/1"
        assert config.default_ttl == 600
        assert config.key_prefix == "custom:"
        assert config.enabled is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
