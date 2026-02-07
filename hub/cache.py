"""
Distributed cache implementation for agent configurations.

This module provides Redis/Valkey-based caching with support for:
- TTL-based cache expiration
- Cache invalidation via pub/sub
- Immediate invalidation on config changes
- Graceful fallback to in-memory cache
"""

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import timedelta
from typing import Any, Dict, Optional, Set

import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool

from botburrow_hub.config import settings

logger = logging.getLogger(__name__)


class CacheConfig:
    """Configuration for cache behavior."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        default_ttl: int = 300,  # 5 minutes default TTL
        key_prefix: str = "botburrow:cache:",
        enabled: bool = True,
    ):
        self.redis_url = redis_url
        self.default_ttl = default_ttl
        self.key_prefix = key_prefix
        self.enabled = enabled


class DistributedCache:
    """Distributed cache with Redis/Valkey backend and in-memory fallback."""

    # Invalidator channel name for pub/sub
    INVALIDATION_CHANNEL = "botburrow:cache:invalidate"

    def __init__(self, config: Optional[CacheConfig] = None):
        self.config = config or self._default_config()
        self._redis: Optional[redis.Redis] = None
        self._pool: Optional[ConnectionPool] = None
        self._memory_cache: Dict[str, Any] = {}
        self._pubsub: Optional = None
        self._listener_task: Optional[asyncio.Task] = None
        self._connected = False

    def _default_config(self) -> CacheConfig:
        """Create default cache configuration from settings."""
        redis_url = getattr(settings, "redis_url", "redis://localhost:6379/0")
        default_ttl = getattr(settings, "cache_ttl", 300)
        enabled = getattr(settings, "cache_enabled", True)

        return CacheConfig(
            redis_url=redis_url,
            default_ttl=default_ttl,
            enabled=enabled,
        )

    async def connect(self) -> bool:
        """Connect to Redis/Valkey.

        Returns True if connection successful, False on fallback to in-memory.
        """
        if not self.config.enabled:
            logger.info("Cache disabled, using in-memory fallback")
            return False

        try:
            # Parse Redis URL
            url = self.config.redis_url

            # Create connection pool
            self._pool = ConnectionPool.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )

            # Create Redis client
            self._redis = redis.Redis(connection_pool=self._pool)

            # Test connection
            await self._redis.ping()

            self._connected = True
            logger.info(f"Connected to Redis at {url}")

            # Start pub/sub listener for invalidation
            self._listener_task = asyncio.create_task(self._listen_for_invalidations())

            return True

        except (redis.ConnectionError, redis.TimeoutError, OSError) as e:
            logger.warning(f"Failed to connect to Redis: {e}. Using in-memory cache.")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from Redis and cleanup resources."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self._pubsub:
            await self._pubsub.close()
            self._pubsub = None

        if self._pool:
            await self._pool.disconnect()
            self._pool = None

        self._redis = None
        self._connected = False

    def _make_key(self, key: str) -> str:
        """Create a full cache key with prefix."""
        return f"{self.config.key_prefix}{key}"

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache.

        Args:
            key: Cache key (without prefix)

        Returns:
            Cached value or None if not found
        """
        cache_key = self._make_key(key)

        if self._connected and self._redis:
            try:
                value = await self._redis.get(cache_key)
                if value is not None:
                    # Deserialize JSON
                    return json.loads(value)
            except (redis.RedisError, json.JSONDecodeError) as e:
                logger.debug(f"Redis get failed for {key}: {e}. Falling back to memory.")

        # Fallback to in-memory cache
        return self._memory_cache.get(cache_key)

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> bool:
        """Set value in cache with optional TTL.

        Args:
            key: Cache key (without prefix)
            value: Value to cache (must be JSON-serializable)
            ttl: Time-to-live in seconds (uses default if not specified)

        Returns:
            True if set successfully, False otherwise
        """
        cache_key = self._make_key(key)
        ttl = ttl or self.config.default_ttl

        # Serialize value
        try:
            serialized = json.dumps(value)
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize cache value for {key}: {e}")
            return False

        success = False

        if self._connected and self._redis:
            try:
                await self._redis.setex(cache_key, ttl, serialized)
                success = True
            except redis.RedisError as e:
                logger.debug(f"Redis set failed for {key}: {e}. Falling back to memory.")

        # Always update in-memory cache as fallback
        self._memory_cache[cache_key] = value

        # Clean up old entries from memory cache periodically
        if len(self._memory_cache) > 1000:
            # Keep only most recent 500 entries
            keys_to_remove = list(self._memory_cache.keys())[:500]
            for k in keys_to_remove:
                del self._memory_cache[k]

        return success

    async def delete(self, key: str) -> bool:
        """Delete value from cache.

        Args:
            key: Cache key (without prefix)

        Returns:
            True if deleted or not found, False on error
        """
        cache_key = self._make_key(key)

        # Delete from Redis
        if self._connected and self._redis:
            try:
                await self._redis.delete(cache_key)
            except redis.RedisError as e:
                logger.debug(f"Redis delete failed for {key}: {e}.")

        # Delete from in-memory cache
        self._memory_cache.pop(cache_key, None)

        return True

    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all cache keys matching a pattern.

        Args:
            pattern: Glob pattern to match (without key prefix)

        Returns:
            Number of keys deleted
        """
        search_pattern = self._make_key(pattern)
        count = 0

        # Delete from Redis using SCAN
        if self._connected and self._redis:
            try:
                async for key in self._redis.scan_iter(match=search_pattern, count=100):
                    await self._redis.delete(key)
                    count += 1
            except redis.RedisError as e:
                logger.debug(f"Redis pattern delete failed: {e}.")

        # Delete from in-memory cache
        keys_to_remove = [
            k for k in self._memory_cache.keys()
            if k.startswith(self.config.key_prefix)
        ]
        for k in keys_to_remove:
            del self._memory_cache[k]
            count += 1

        return count

    async def invalidate_all(self) -> int:
        """Invalidate all cached values.

        Returns:
            Number of keys deleted
        """
        return await self.invalidate_pattern("*")

    async def publish_invalidation(
        self,
        agent_name: Optional[str] = None,
        config_source: Optional[str] = None,
    ) -> None:
        """Publish cache invalidation event to all runners.

        This is called when agent configs change via git webhook.
        Runners listening to the pub/sub channel will invalidate their cache.

        Args:
            agent_name: Specific agent to invalidate (None for all)
            config_source: Git repo URL of changed config (None for all)
        """
        if not self._connected or not self._redis:
            logger.debug("Redis not connected, skipping invalidation broadcast")
            return

        message = {
            "type": "invalidate",
            "agent_name": agent_name,
            "config_source": config_source,
            "timestamp": asyncio.get_event_loop().time(),
        }

        try:
            await self._redis.publish(
                self.INVALIDATION_CHANNEL,
                json.dumps(message),
            )
            logger.info(
                f"Published cache invalidation: agent={agent_name}, "
                f"source={config_source}"
            )
        except redis.RedisError as e:
            logger.error(f"Failed to publish invalidation: {e}")

    async def _listen_for_invalidations(self) -> None:
        """Listen for cache invalidation messages from other runners.

        This runs as a background task and invalidates local cache when
        a config change webhook is received.
        """
        if not self._connected or not self._redis:
            return

        try:
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(self.INVALIDATION_CHANNEL)

            async for message in self._pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await self._handle_invalidation(data)
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Invalid invalidation message: {e}")

        except asyncio.CancelledError:
            # Task was cancelled, exit gracefully
            if self._pubsub:
                await self._pubsub.unsubscribe(self.INVALIDATION_CHANNEL)
            raise
        except redis.RedisError as e:
            logger.error(f"Pub/sub listener error: {e}")

    async def _handle_invalidation(self, data: Dict[str, Any]) -> None:
        """Handle an invalidation message.

        Args:
            data: Invalidated message with agent_name and/or config_source
        """
        agent_name = data.get("agent_name")
        config_source = data.get("config_source")

        # Build cache key pattern to invalidate
        if agent_name and config_source:
            # Invalidate specific agent from specific source
            pattern = f"agent:{agent_name}:{config_source}"
        elif agent_name:
            # Invalidate all configs for this agent
            pattern = f"agent:{agent_name}:*"
        elif config_source:
            # Invalidate all agents from this source
            # Need to search by matching config_source in cached values
            await self._invalidate_by_source(config_source)
            return
        else:
            # Invalidate everything
            pattern = "*"

        count = await self.invalidate_pattern(pattern)
        logger.info(f"Invalidated {count} cache entries for pattern: {pattern}")

    async def _invalidate_by_source(self, config_source: str) -> int:
        """Invalidate all cache entries from a specific config source.

        This requires scanning memory cache since we can't do it efficiently
        in Redis without indexing by source.

        Args:
            config_source: Git repository URL

        Returns:
            Number of keys invalidated
        """
        count = 0
        keys_to_delete = []

        # Scan in-memory cache
        for key, value in self._memory_cache.items():
            if isinstance(value, dict) and value.get("config_source") == config_source:
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del self._memory_cache[key]
            # Also try to delete from Redis
            if self._connected and self._redis:
                try:
                    await self._redis.delete(key)
                except redis.RedisError:
                    pass
            count += 1

        # For Redis, we need to scan all agent config keys
        if self._connected and self._redis:
            try:
                pattern = self._make_key("agent:*")
                async for key in self._redis.scan_iter(match=pattern, count=100):
                    # Get the value to check config_source
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
                logger.debug(f"Error scanning Redis for source invalidation: {e}")

        return count

    async def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        stats = {
            "connected": self._connected,
            "memory_cache_size": len(self._memory_cache),
            "type": "redis" if self._connected else "memory",
        }

        if self._connected and self._redis:
            try:
                info = await self._redis.info("stats")
                stats.update({
                    "redis_keyspace_hits": info.get("keyspace_hits", 0),
                    "redis_keyspace_misses": info.get("keyspace_misses", 0),
                })
            except redis.RedisError:
                pass

        return stats


# Global cache instance
_cache: Optional[DistributedCache] = None


async def get_cache() -> DistributedCache:
    """Get or create the global cache instance.

    The cache is lazily initialized on first use.
    """
    global _cache

    if _cache is None:
        _cache = DistributedCache()
        await _cache.connect()

    return _cache


async def close_cache() -> None:
    """Close the global cache instance."""
    global _cache

    if _cache:
        await _cache.disconnect()
        _cache = None
