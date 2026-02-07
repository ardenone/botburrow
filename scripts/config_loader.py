#!/usr/bin/env python3
"""
Multi-Repository Agent Config Loader

This module provides functionality for loading agent configurations from
multiple git repositories. It is used by agent runners to fetch agent
definitions from configured sources.

Features:
- Distributed caching with Redis/Valkey and in-memory fallback
- Cache invalidation via pub/sub for immediate updates
- TTL-based cache expiration
- Git repository management with parallel clone/pull

Usage:
    from config_loader import AgentConfigLoader

    loader = AgentConfigLoader(repos_config_path="/etc/config/repos.json")

    # Find and load a specific agent's config
    config = loader.load_agent_config(
        agent_name="claude-coder-1",
        config_source="https://github.com/org/agents.git"
    )

    # Refresh all repositories
    await loader.refresh_all_repos()

    # Invalidate specific agent cache
    await loader.invalidate_agent("claude-coder-1")
"""

import asyncio
import json
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Try to import distributed cache, fall back to simple cache
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.debug("redis not available, using in-memory cache only")


# ============================================================================
# Distributed Cache Implementation
# ============================================================================

class CacheConfig:
    """Configuration for cache behavior."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        default_ttl: int = 300,  # 5 minutes default TTL
        key_prefix: str = "botburrow:agent:",
        enabled: bool = True,
    ):
        self.redis_url = redis_url
        self.default_ttl = default_ttl
        self.key_prefix = key_prefix
        self.enabled = enabled


class AgentConfigCache:
    """Cache for agent configurations with Redis/Valkey backend and in-memory fallback.

    This cache provides:
    - TTL-based expiration (default 5 minutes)
    - Immediate invalidation via pub/sub
    - Graceful fallback to in-memory if Redis unavailable
    """

    INVALIDATION_CHANNEL = "botburrow:agent:invalidate"

    def __init__(self, config: Optional[CacheConfig] = None):
        self.config = config or CacheConfig()
        self._redis: Optional[redis.Redis] = None
        self._pool: Optional = None
        self._memory_cache: Dict[str, Dict[str, Any]] = {}
        self._pubsub_task: Optional[asyncio.Task] = None
        self._connected = False

    async def connect(self) -> bool:
        """Connect to Redis/Valkey.

        Returns True if connection successful, False on fallback to in-memory.
        """
        if not REDIS_AVAILABLE or not self.config.enabled:
            logger.info("Redis cache disabled, using in-memory only")
            return False

        try:
            # Get Redis URL from environment or config
            redis_url = os.environ.get("REDIS_URL", self.config.redis_url)

            self._pool = redis.ConnectionPool.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            self._redis = redis.Redis(connection_pool=self._pool)

            # Test connection
            await self._redis.ping()

            self._connected = True
            logger.info(f"Connected to Redis at {redis_url}")

            # Start pub/sub listener
            self._pubsub_task = asyncio.create_task(self._listen_for_invalidations())

            return True

        except (redis.ConnectionError, redis.TimeoutError, OSError) as e:
            logger.warning(f"Failed to connect to Redis: {e}. Using in-memory cache.")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from Redis and cleanup resources."""
        if self._pubsub_task:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass

        if self._pool:
            await self._pool.disconnect()
            self._pool = None

        self._redis = None
        self._connected = False

    def _make_key(self, agent_name: str, config_source: Optional[str] = None) -> str:
        """Create a cache key."""
        source = config_source or "default"
        return f"{self.config.key_prefix}{agent_name}:{source}"

    async def get(
        self,
        agent_name: str,
        config_source: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get agent config from cache.

        Args:
            agent_name: Name of the agent
            config_source: Git repo URL (optional)

        Returns:
            Cached config dict or None
        """
        cache_key = self._make_key(agent_name, config_source)

        if self._connected and self._redis:
            try:
                value = await self._redis.get(cache_key)
                if value:
                    return json.loads(value)
            except (redis.RedisError, json.JSONDecodeError) as e:
                logger.debug(f"Redis get failed: {e}. Falling back to memory.")

        return self._memory_cache.get(cache_key)

    async def set(
        self,
        agent_name: str,
        config: Dict[str, Any],
        config_source: Optional[str] = None,
        ttl: Optional[int] = None,
    ) -> bool:
        """Cache agent config with TTL.

        Args:
            agent_name: Name of the agent
            config: Agent config dict to cache
            config_source: Git repo URL (optional)
            ttl: Time-to-live in seconds (uses default if not specified)

        Returns:
            True if cached successfully
        """
        cache_key = self._make_key(agent_name, config_source)
        ttl = ttl or self.config.default_ttl

        try:
            serialized = json.dumps(config, default=str)
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize config for {agent_name}: {e}")
            return False

        success = False

        if self._connected and self._redis:
            try:
                await self._redis.setex(cache_key, ttl, serialized)
                success = True
            except redis.RedisError as e:
                logger.debug(f"Redis set failed: {e}. Falling back to memory.")

        # Always update in-memory cache as fallback
        self._memory_cache[cache_key] = config

        # Limit memory cache size
        if len(self._memory_cache) > 1000:
            keys_to_remove = list(self._memory_cache.keys())[:500]
            for k in keys_to_remove:
                del self._memory_cache[k]

        return success

    async def delete(
        self,
        agent_name: str,
        config_source: Optional[str] = None,
    ) -> bool:
        """Delete agent config from cache.

        Args:
            agent_name: Name of the agent
            config_source: Git repo URL (optional)

        Returns:
            True if deleted or not found
        """
        cache_key = self._make_key(agent_name, config_source)

        if self._connected and self._redis:
            try:
                await self._redis.delete(cache_key)
            except redis.RedisError:
                pass

        self._memory_cache.pop(cache_key, None)
        return True

    async def invalidate_by_source(self, config_source: str) -> int:
        """Invalidate all cache entries from a specific git repository.

        Args:
            config_source: Git repository URL

        Returns:
            Number of entries invalidated
        """
        count = 0

        # Invalidate from in-memory cache
        keys_to_remove = []
        for key, value in self._memory_cache.items():
            if value.get("config_source") == config_source:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._memory_cache[key]
            count += 1

        # Invalidate from Redis by scanning keys
        if self._connected and self._redis:
            try:
                pattern = f"{self.config.key_prefix}*"
                async for key in self._redis.scan_iter(match=pattern, count=100):
                    try:
                        value = await self._redis.get(key)
                        if value:
                            data = json.loads(value)
                            if data.get("config_source") == config_source:
                                await self._redis.delete(key)
                                count += 1
                    except (redis.RedisError, json.JSONDecodeError):
                        continue
            except redis.RedisError as e:
                logger.debug(f"Error scanning Redis: {e}")

        return count

    async def clear(self) -> None:
        """Clear all cached entries."""
        self._memory_cache.clear()

        if self._connected and self._redis:
            try:
                pattern = f"{self.config.key_prefix}*"
                async for key in self._redis.scan_iter(match=pattern, count=100):
                    await self._redis.delete(key)
            except redis.RedisError as e:
                logger.debug(f"Error clearing Redis cache: {e}")

    async def publish_invalidation(
        self,
        agent_name: Optional[str] = None,
        config_source: Optional[str] = None,
    ) -> None:
        """Publish cache invalidation event to all runners.

        Args:
            agent_name: Specific agent to invalidate (None for all)
            config_source: Git repo URL (None for all)
        """
        if not self._connected or not self._redis:
            return

        message = {
            "type": "invalidate",
            "agent_name": agent_name,
            "config_source": config_source,
        }

        try:
            await self._redis.publish(
                self.INVALIDATION_CHANNEL,
                json.dumps(message),
            )
            logger.info(f"Published invalidation: agent={agent_name}, source={config_source}")
        except redis.RedisError as e:
            logger.error(f"Failed to publish invalidation: {e}")

    async def _listen_for_invalidations(self) -> None:
        """Listen for cache invalidation messages from other runners."""
        if not self._connected or not self._redis:
            return

        try:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(self.INVALIDATION_CHANNEL)

            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await self._handle_invalidation(data)
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Invalid invalidation message: {e}")

        except asyncio.CancelledError:
            if pubsub:
                await pubsub.unsubscribe(self.INVALIDATION_CHANNEL)
            raise
        except redis.RedisError as e:
            logger.error(f"Pub/sub listener error: {e}")

    async def _handle_invalidation(self, data: Dict[str, Any]) -> None:
        """Handle an invalidation message."""
        agent_name = data.get("agent_name")
        config_source = data.get("config_source")

        if agent_name and config_source:
            # Invalidate specific agent from specific source
            await self.delete(agent_name, config_source)
        elif agent_name:
            # Invalidate all configs for this agent
            cache_key = f"{self.config.key_prefix}{agent_name}:*"
            if self._connected and self._redis:
                try:
                    async for key in self._redis.scan_iter(match=cache_key, count=100):
                        await self._redis.delete(key)
                except redis.RedisError:
                    pass
            # Also clear from memory
            keys_to_remove = [
                k for k in self._memory_cache.keys()
                if k.startswith(f"{self.config.key_prefix}{agent_name}:")
            ]
            for k in keys_to_remove:
                del self._memory_cache[k]
        elif config_source:
            # Invalidate all agents from this source
            await self.invalidate_by_source(config_source)
        else:
            # Invalidate everything
            await self.clear()

        logger.info(f"Handled invalidation: agent={agent_name}, source={config_source}")


@dataclass
class RepoConfig:
    """Configuration for a single git repository."""

    name: str
    url: str
    branch: str = "main"
    auth_type: str = "none"  # none, token, ssh
    auth_secret: Optional[str] = None
    clone_path: str = "/configs/default"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RepoConfig":
        """Create RepoConfig from dictionary."""
        return cls(
            name=data.get("name", "unknown"),
            url=data["url"],
            branch=data.get("branch", "main"),
            auth_type=data.get("auth_type", "none"),
            auth_secret=data.get("auth_secret"),
            clone_path=data.get("clone_path", f"/configs/{data.get('name', 'default')}"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert RepoConfig to dictionary."""
        return {
            "name": self.name,
            "url": self.url,
            "branch": self.branch,
            "auth_type": self.auth_type,
            "auth_secret": self.auth_secret,
            "clone_path": self.clone_path,
        }


@dataclass
class AgentConfig:
    """Agent configuration loaded from repository."""

    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    type: str = "native"
    brain: Dict[str, Any] = None
    capabilities: Dict[str, Any] = None
    interests: Dict[str, Any] = None
    behavior: Dict[str, Any] = None
    memory: Dict[str, Any] = None
    system_prompt: Optional[str] = None
    config_source: Optional[str] = None
    config_path: Optional[str] = None
    config_branch: str = "main"

    def __post_init__(self):
        if self.brain is None:
            self.brain = {}
        if self.capabilities is None:
            self.capabilities = {}
        if self.interests is None:
            self.interests = {}
        if self.behavior is None:
            self.behavior = {}
        if self.memory is None:
            self.memory = {}

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        system_prompt: Optional[str] = None,
        config_source: Optional[str] = None,
        config_path: Optional[str] = None,
        config_branch: str = "main",
    ) -> "AgentConfig":
        """Create AgentConfig from dictionary."""
        return cls(
            name=data.get("name", ""),
            display_name=data.get("display_name"),
            description=data.get("description"),
            type=data.get("type", "native"),
            brain=data.get("brain", {}),
            capabilities=data.get("capabilities", {}),
            interests=data.get("interests", {}),
            behavior=data.get("behavior", {}),
            memory=data.get("memory", {}),
            system_prompt=system_prompt,
            config_source=config_source,
            config_path=config_path,
            config_branch=config_branch,
        )


class GitRepositoryManager:
    """Manages git repository operations for multiple repos."""

    def __init__(
        self,
        repos: List[RepoConfig],
        clone_depth: int = 1,
        timeout: int = 30,
        max_workers: int = 4,
    ):
        self.repos = repos
        self.clone_depth = clone_depth
        self.timeout = timeout
        self.max_workers = max_workers

    def _build_git_url(self, repo: RepoConfig) -> str:
        """Build authenticated git URL if needed."""
        if repo.auth_type == "token" and repo.auth_secret:
            # For HTTPS with token, we need to inject the token
            # This is handled via GIT_ASKPASS or git credential helper
            return repo.url
        elif repo.auth_type == "ssh":
            # SSH URLs already contain auth via ssh key
            return repo.url
        else:
            return repo.url

    def _get_auth_env(self, repo: RepoConfig) -> Dict[str, str]:
        """Get environment variables for authentication."""
        env = os.environ.copy()

        if repo.auth_type == "token" and repo.auth_secret:
            # Try to read token from secret file or environment
            token = self._read_secret(repo.auth_secret)
            if token:
                # Use GIT_ASKPASS mechanism for token auth
                env["GIT_USERNAME"] = "token"
                env["GIT_PASSWORD"] = token
                env["GIT_TERMINAL_PROMPT"] = "0"

        return env

    def _read_secret(self, secret_ref: str) -> Optional[str]:
        """Read secret from file or Kubernetes secret mount."""
        # Try Kubernetes secret mount path first
        secret_path = Path(f"/etc/secrets/{secret_ref}/token")
        if secret_path.exists():
            return secret_path.read_text().strip()

        # Try environment variable
        env_name = secret_ref.upper().replace("-", "_")
        return os.environ.get(env_name)

    def clone_repo(self, repo: RepoConfig) -> bool:
        """Clone a single repository.

        Returns True if successful, False otherwise.
        """
        clone_path = Path(repo.clone_path)

        # Create parent directory if needed
        clone_path.parent.mkdir(parents=True, exist_ok=True)

        # Check if already exists
        if clone_path.exists():
            logger.debug(f"Repository {repo.name} already exists at {clone_path}")
            return self.pull_repo(repo)

        logger.info(f"Cloning repository: {repo.name} from {repo.url}")

        cmd = [
            "git",
            "clone",
            "--depth", str(self.clone_depth),
            "--single-branch",
            "--branch", repo.branch,
            self._build_git_url(repo),
            str(clone_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._get_auth_env(repo),
            )
            if result.returncode != 0:
                logger.error(f"Git clone failed for {repo.name}: {result.stderr}")
                return False
            logger.info(f"Repository {repo.name} cloned to {clone_path}")
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"Git clone timeout for {repo.name}")
            return False
        except Exception as e:
            logger.error(f"Git clone error for {repo.name}: {e}")
            return False

    def pull_repo(self, repo: RepoConfig) -> bool:
        """Pull latest changes for a single repository.

        Returns True if successful, False otherwise.
        """
        clone_path = Path(repo.clone_path)

        if not clone_path.exists():
            return self.clone_repo(repo)

        logger.info(f"Pulling repository: {repo.name}")

        cmd = ["git", "-C", str(clone_path), "pull", "origin", repo.branch]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._get_auth_env(repo),
            )
            if result.returncode != 0:
                logger.warning(f"Git pull failed for {repo.name}: {result.stderr}")
                return False
            logger.info(f"Repository {repo.name} updated")
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"Git pull timeout for {repo.name}")
            return False
        except Exception as e:
            logger.error(f"Git pull error for {repo.name}: {e}")
            return False

    def clone_or_pull_all(self) -> Dict[str, bool]:
        """Clone or pull all repositories in parallel.

        Returns a dictionary mapping repo names to success status.
        """
        results = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_repo = {
                executor.submit(self.clone_repo, repo): repo.name
                for repo in self.repos
            }

            for future in future_to_repo:
                repo_name = future_to_repo[future]
                try:
                    results[repo_name] = future.result()
                except Exception as e:
                    logger.error(f"Error processing {repo_name}: {e}")
                    results[repo_name] = False

        return results

    async def refresh_all_repos_async(self) -> Dict[str, bool]:
        """Async wrapper for refresh_all_repos."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.clone_or_pull_all)


class AgentConfigLoader:
    """Load agent configs from multiple git repositories.

    Features:
    - Distributed caching with Redis/Valkey
    - In-memory cache fallback
    - Cache invalidation via pub/sub
    - TTL-based cache expiration
    """

    def __init__(
        self,
        repos_config_path: str = "/etc/config/repos.json",
        clone_depth: int = 1,
        timeout: int = 30,
        max_workers: int = 4,
        cache_ttl: int = 300,  # 5 minutes default
        enable_cache: bool = True,
    ):
        self.repos = self._load_repos_config(repos_config_path)
        self.clone_depth = clone_depth
        self.timeout = timeout
        self.max_workers = max_workers
        self.git_manager = GitRepositoryManager(
            repos=self.repos,
            clone_depth=clone_depth,
            timeout=timeout,
            max_workers=max_workers,
        )

        # Initialize distributed cache
        self.enable_cache = enable_cache
        self.cache_ttl = cache_ttl
        self.cache: Optional[AgentConfigCache] = None

        # Legacy in-memory cache (kept as fallback)
        self.config_cache: Dict[str, AgentConfig] = {}

    async def initialize_cache(self) -> bool:
        """Initialize the distributed cache connection.

        Returns True if cache is available, False otherwise.
        """
        if not self.enable_cache:
            return False

        self.cache = AgentConfigCache(
            config=CacheConfig(default_ttl=self.cache_ttl)
        )
        connected = await self.cache.connect()

        if connected:
            logger.info("Distributed cache initialized")
        else:
            logger.info("Using in-memory cache only")

        return connected

    async def close_cache(self) -> None:
        """Close the distributed cache connection."""
        if self.cache:
            await self.cache.disconnect()

    async def invalidate_agent(
        self,
        agent_name: str,
        config_source: Optional[str] = None,
    ) -> None:
        """Invalidate cache for a specific agent.

        Args:
            agent_name: Name of the agent to invalidate
            config_source: Git repo URL (optional)
        """
        # Clear from distributed cache
        if self.cache:
            await self.cache.delete(agent_name, config_source)

        # Clear from in-memory cache
        pattern = f"{agent_name}:{config_source or 'any'}"
        self.config_cache.pop(pattern, None)

        # Also clear any variations
        keys_to_remove = [
            k for k in self.config_cache.keys()
            if k.startswith(f"{agent_name}:")
        ]
        for k in keys_to_remove:
            del self.config_cache[k]

        logger.info(f"Invalidated cache for agent: {agent_name}")

    async def invalidate_by_source(self, config_source: str) -> None:
        """Invalidate all cache entries from a specific git repository.

        Args:
            config_source: Git repository URL
        """
        if self.cache:
            count = await self.cache.invalidate_by_source(config_source)
            logger.info(f"Invalidated {count} cache entries for source: {config_source}")

        # Clear from in-memory cache
        keys_to_remove = [
            k for k, v in self.config_cache.items()
            if v.config_source == config_source
        ]
        for k in keys_to_remove:
            del self.config_cache[k]

    def _load_repos_config(self, path: str) -> List[RepoConfig]:
        """Load repository configuration from JSON file."""
        try:
            with open(path) as f:
                data = json.load(f)
            return [RepoConfig.from_dict(repo) for repo in data]
        except FileNotFoundError:
            logger.warning(f"Repos config file not found: {path}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Invalid repos config JSON: {e}")
            return []

    def _urls_match(self, url1: str, url2: str) -> bool:
        """Check if two git URLs refer to the same repository.

        Handles various URL formats (https, git@, .git suffix).
        """
        def normalize_url(url: str) -> str:
            # Remove protocol
            url = url.replace("https://", "").replace("http://", "")
            # Remove git@ prefix
            url = url.replace("git@", "")
            # Remove .git suffix
            url = url.removesuffix(".git")
            # Remove : after host (for SSH URLs)
            url = url.replace(":", "/", 1)
            return url.lower()

        return normalize_url(url1) == normalize_url(url2)

    def find_agent_config(
        self,
        agent_name: str,
        config_source: Optional[str] = None,
    ) -> Optional[Path]:
        """Find agent config in the correct repository.

        Args:
            agent_name: Name of the agent to find
            config_source: Git repo URL where config should be located

        Returns:
            Path to config.yaml if found, None otherwise
        """
        # First try to match by config_source
        if config_source:
            for repo in self.repos:
                if self._urls_match(repo.url, config_source):
                    config_path = (
                        Path(repo.clone_path) / "agents" / agent_name / "config.yaml"
                    )
                    if config_path.exists():
                        logger.debug(
                            f"Found config for {agent_name} in {repo.name} "
                            f"(matched config_source)"
                        )
                        return config_path

        # Fallback: search all repos
        for repo in self.repos:
            config_path = (
                Path(repo.clone_path) / "agents" / agent_name / "config.yaml"
            )
            if config_path.exists():
                logger.debug(
                    f"Found config for {agent_name} in {repo.name} (fallback search)"
                )
                return config_path

        logger.warning(f"Config for {agent_name} not found in any repo")
        return None

    def find_repo_by_config_source(self, config_source: str) -> Optional[RepoConfig]:
        """Find repository configuration by config_source URL."""
        for repo in self.repos:
            if self._urls_match(repo.url, config_source):
                return repo
        return None

    def load_agent_config(
        self,
        agent_name: str,
        config_source: Optional[str] = None,
    ) -> Optional[AgentConfig]:
        """Load agent configuration from git.

        Args:
            agent_name: Name of the agent to load
            config_source: Git repo URL where config should be located

        Returns:
            AgentConfig if found and loaded, None otherwise
        """
        cache_key = f"{agent_name}:{config_source or 'any'}"

        # Check distributed cache first
        if self.cache:
            cached = asyncio.run(self.cache.get(agent_name, config_source))
            if cached:
                # Reconstruct AgentConfig from cached dict
                return AgentConfig.from_dict(
                    cached.get("data", {}),
                    system_prompt=cached.get("system_prompt"),
                    config_source=cached.get("config_source"),
                    config_path=cached.get("config_path"),
                    config_branch=cached.get("config_branch", "main"),
                )

        # Check in-memory cache fallback
        if cache_key in self.config_cache:
            return self.config_cache[cache_key]

        # Find config file
        config_path = self.find_agent_config(agent_name, config_source)
        if not config_path:
            return None

        try:
            # Load config.yaml
            with open(config_path) as f:
                config_data = yaml.safe_load(f)

            # Load system-prompt.md
            prompt_path = config_path.parent / "system-prompt.md"
            system_prompt = None
            if prompt_path.exists():
                with open(prompt_path) as f:
                    system_prompt = f.read()

            # Find the repo for this agent
            repo = None
            if config_source:
                repo = self.find_repo_by_config_source(config_source)
            else:
                for r in self.repos:
                    if config_path.is_relative_to(r.clone_path):
                        repo = r
                        break

            config = AgentConfig.from_dict(
                config_data,
                system_prompt=system_prompt,
                config_source=repo.url if repo else None,
                config_path=f"agents/{agent_name}",
                config_branch=repo.branch if repo else "main",
            )

            # Cache the result (both distributed and in-memory)
            self.config_cache[cache_key] = config

            if self.cache:
                # Cache as dict for JSON serialization
                cache_dict = {
                    "data": asdict(config),
                    "system_prompt": system_prompt,
                    "config_source": config.config_source,
                    "config_path": config.config_path,
                    "config_branch": config.config_branch,
                }
                asyncio.run(self.cache.set(
                    agent_name,
                    cache_dict,
                    config_source,
                ))

            return config

        except Exception as e:
            logger.error(f"Failed to load config for {agent_name}: {e}")
            return None

    async def load_agent_config_async(
        self,
        agent_name: str,
        config_source: Optional[str] = None,
    ) -> Optional[AgentConfig]:
        """Async version of load_agent_config.

        Args:
            agent_name: Name of the agent to load
            config_source: Git repo URL where config should be located

        Returns:
            AgentConfig if found and loaded, None otherwise
        """
        cache_key = f"{agent_name}:{config_source or 'any'}"

        # Check distributed cache first
        if self.cache:
            cached = await self.cache.get(agent_name, config_source)
            if cached:
                # Reconstruct AgentConfig from cached dict
                return AgentConfig.from_dict(
                    cached.get("data", {}),
                    system_prompt=cached.get("system_prompt"),
                    config_source=cached.get("config_source"),
                    config_path=cached.get("config_path"),
                    config_branch=cached.get("config_branch", "main"),
                )

        # Check in-memory cache fallback
        if cache_key in self.config_cache:
            return self.config_cache[cache_key]

        # Find config file
        config_path = self.find_agent_config(agent_name, config_source)
        if not config_path:
            return None

        try:
            # Load config.yaml
            with open(config_path) as f:
                config_data = yaml.safe_load(f)

            # Load system-prompt.md
            prompt_path = config_path.parent / "system-prompt.md"
            system_prompt = None
            if prompt_path.exists():
                with open(prompt_path) as f:
                    system_prompt = f.read()

            # Find the repo for this agent
            repo = None
            if config_source:
                repo = self.find_repo_by_config_source(config_source)
            else:
                for r in self.repos:
                    if config_path.is_relative_to(r.clone_path):
                        repo = r
                        break

            config = AgentConfig.from_dict(
                config_data,
                system_prompt=system_prompt,
                config_source=repo.url if repo else None,
                config_path=f"agents/{agent_name}",
                config_branch=repo.branch if repo else "main",
            )

            # Cache the result (both distributed and in-memory)
            self.config_cache[cache_key] = config

            if self.cache:
                # Cache as dict for JSON serialization
                cache_dict = {
                    "data": asdict(config),
                    "system_prompt": system_prompt,
                    "config_source": config.config_source,
                    "config_path": config.config_path,
                    "config_branch": config.config_branch,
                }
                await self.cache.set(
                    agent_name,
                    cache_dict,
                    config_source,
                )

            return config

        except Exception as e:
            logger.error(f"Failed to load config for {agent_name}: {e}")
            return None

    def list_agents(self) -> Dict[str, List[str]]:
        """List all agents found in all repositories.

        Returns:
            Dictionary mapping repo names to lists of agent names
        """
        agents_by_repo = {}

        for repo in self.repos:
            agents_dir = Path(repo.clone_path) / "agents"
            agents = []

            if agents_dir.exists():
                for agent_dir in agents_dir.iterdir():
                    if agent_dir.is_dir():
                        config_file = agent_dir / "config.yaml"
                        if config_file.exists():
                            agents.append(agent_dir.name)

            agents_by_repo[repo.name] = agents

        return agents_by_repo

    def refresh_all_repos(self) -> Dict[str, bool]:
        """Pull latest changes from all repos.

        Returns:
            Dictionary mapping repo names to success status
        """
        logger.info("Refreshing all agent repositories")
        results = self.git_manager.clone_or_pull_all()

        # Clear cache after refresh
        self.config_cache.clear()

        success_count = sum(1 for r in results.values() if r)
        logger.info(f"Refreshed {success_count}/{len(results)} repositories")

        return results

    async def refresh_all_repos_async(self) -> Dict[str, bool]:
        """Async version of refresh_all_repos."""
        return await self.git_manager.refresh_all_repos_async()


def load_repos_config(path: str) -> List[Dict[str, Any]]:
    """Load repository configuration from JSON file.

    Utility function for backward compatibility.
    """
    with open(path) as f:
        return json.load(f)


def get_git_info() -> tuple[str, str]:
    """Get current git commit SHA and branch.

    Returns:
        Tuple of (commit_sha, branch)
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        commit_sha = result.stdout.strip() if result.returncode == 0 else "unknown"

        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = result.stdout.strip() if result.returncode == 0 else "unknown"

        return commit_sha, branch
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown", "unknown"


# CLI for testing
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Agent Config Loader CLI")
    parser.add_argument(
        "--repos-file",
        default="/etc/config/repos.json",
        help="Path to repos.json configuration",
    )
    parser.add_argument(
        "--agent",
        help="Name of agent to load",
    )
    parser.add_argument(
        "--config-source",
        help="Git repo URL where agent config is located",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all agents in all repos",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh all repositories",
    )

    args = parser.parse_args()

    loader = AgentConfigLoader(repos_config_path=args.repos_file)

    if args.refresh:
        results = loader.refresh_all_repos()
        for repo, success in results.items():
            status = "OK" if success else "FAILED"
            print(f"{repo}: {status}")

    if args.list:
        agents = loader.list_agents()
        for repo, agent_list in agents.items():
            print(f"{repo}: {', '.join(agent_list) if agent_list else '(no agents)'}")

    if args.agent:
        config = loader.load_agent_config(args.agent, args.config_source)
        if config:
            print(f"Agent: {config.name}")
            print(f"Type: {config.type}")
            print(f"Display Name: {config.display_name or 'N/A'}")
            print(f"Config Source: {config.config_source or 'N/A'}")
            print(f"System Prompt: {len(config.system_prompt or 0)} chars")
        else:
            print(f"Agent '{args.agent}' not found")
