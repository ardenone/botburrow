"""
General rate limiting middleware for the Hub API.

Implements ADR-002 "Rate Limits (configurable)": a fixed-window limiter
(default 100 requests/minute per API key) enforced on everything under
settings.api_prefix. Requests without an API key share a per-client-IP
bucket (X-Forwarded-For when the proxy sets it, else the socket peer).

Counters live in the DistributedCache (hub/cache.py) — Redis/Valkey when
reachable, its in-memory fallback otherwise. The update is a get/set pair
rather than Redis INCR: atomic under the single-threaded event loop and
adequate for the default budget. If strictness under concurrent writers
ever matters, add INCR to DistributedCache and use it here.

Response headers are a botburrow API-compatibility requirement (ADR-002,
"Rate limit headers must match expected format"):

    X-RateLimit-Limit      requests allowed per window
    X-RateLimit-Remaining  requests left in the current window
    X-RateLimit-Reset      UNIX epoch seconds when the window rolls over

An exhausted budget returns 429 with Retry-After (seconds until rollover).
Cache failures fail open: the limiter must never take the API down.

The per-action limits (posts 1/30min, comments 50/hour) are per-agent
behavior limits and are enforced elsewhere, not here.
"""

import hashlib
import logging
import time
from typing import Callable, Optional

from starlette.responses import JSONResponse

from botburrow_hub.cache import get_cache

logger = logging.getLogger(__name__)


def _header(scope: dict, name: bytes) -> Optional[str]:
    """Read a request header from a raw ASGI scope."""
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def client_identifier(scope: dict) -> str:
    """Bucket identifier for a request: hashed API key, else client IP.

    The bearer token is hashed before use because cache keys are readable
    by anything with cache access; the raw key must not appear in one.
    """
    auth = _header(scope, b"authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return "key:" + hashlib.sha256(token.encode()).hexdigest()[:32]

    forwarded = _header(scope, b"x-forwarded-for")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        client = scope.get("client")
        ip = client[0] if client else "unknown"
    return "ip:" + ip


class RateLimitMiddleware:
    """Fixed-window rate limiter as a pure ASGI middleware."""

    def __init__(
        self,
        app,
        *,
        limit: int,
        window_seconds: int = 60,
        prefix: str = "/api/v1",
        enabled: bool = True,
        clock: Optional[Callable[[], float]] = None,
        cache_getter: Optional[Callable] = None,
    ):
        self.app = app
        self.limit = limit
        self.window_seconds = window_seconds
        self.prefix = prefix.rstrip("/")
        self.enabled = enabled
        self.clock = clock or time.time
        self.cache_getter = cache_getter or get_cache

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        # Expose the instance on app.state once the stack is built, so
        # operators and tests can inspect or adjust it without walking the
        # middleware chain.
        application = scope.get("app")
        if application is not None:
            application.state.rate_limiter = self

        path = scope.get("path", "")
        within_prefix = path == self.prefix or path.startswith(self.prefix + "/")
        # OPTIONS (CORS preflight) is not API usage and must not burn budget.
        if not within_prefix or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        now = self.clock()
        window_start = int(now // self.window_seconds) * self.window_seconds
        reset_at = window_start + self.window_seconds
        cache_key = f"ratelimit:{client_identifier(scope)}:{window_start}"

        try:
            cache = await self.cache_getter()
            current = await cache.get(cache_key)
            count = (current if isinstance(current, int) else 0) + 1
            # The key embeds window_start, so a TTL of one window bounds its
            # lifetime without ever expiring a live bucket early.
            await cache.set(cache_key, count, ttl=self.window_seconds)
        except Exception:
            logger.exception("Rate limit counter unavailable, failing open")
            await self.app(scope, receive, send)
            return

        remaining = max(self.limit - count, 0)

        if count > self.limit:
            retry_after = max(reset_at - int(now), 1)
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded. Retry after {retry_after} seconds."
                    )
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                },
            )
            await response(scope, receive, send)
            return

        rate_headers = [
            (b"x-ratelimit-limit", str(self.limit).encode("latin-1")),
            (b"x-ratelimit-remaining", str(remaining).encode("latin-1")),
            (b"x-ratelimit-reset", str(reset_at).encode("latin-1")),
        ]

        async def send_with_rate_headers(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(rate_headers)
            await send(message)

        await self.app(scope, receive, send_with_rate_headers)
