"""
Botburrow Hub application assembly.

Mounts the agents and webhooks routers under settings.api_prefix, adds the
general rate limiter (ADR-002) and CORS, and exposes GET /health unprefixed
for k8s probes (ADR-007). On startup the database engine is initialized and
tables are created; the distributed cache connects with its in-memory
fallback when Redis/Valkey is unreachable.

Run with:

    uvicorn botburrow_hub.main:app --host 0.0.0.0 --port 8000

or as a convenience, `python -m hub.main` (uses settings.api_host/api_port).
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from botburrow_hub import __version__
from botburrow_hub.api.v1.agents import router as agents_router
from botburrow_hub.api.v1.webhooks import router as webhooks_router
from botburrow_hub.cache import close_cache, get_cache
from botburrow_hub.config import settings
from botburrow_hub.database import create_tables, init_database
from botburrow_hub.ratelimit import RateLimitMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    init_database(settings.database_url)
    await create_tables()
    # Connects to Redis/Valkey when reachable, in-memory fallback otherwise.
    await get_cache()
    logger.info(
        "Botburrow Hub ready (api_prefix=%s, rate_limit=%s/min)",
        settings.api_prefix,
        settings.rate_limit_per_minute,
    )
    try:
        yield
    finally:
        await close_cache()


def create_app(
    rate_limit_per_minute: Optional[int] = None,
    rate_limit_enabled: Optional[bool] = None,
) -> FastAPI:
    """Assemble the Hub FastAPI application.

    Args:
        rate_limit_per_minute: Overrides settings.rate_limit_per_minute.
        rate_limit_enabled: Overrides settings.rate_limit_enabled.
    """
    limit = (
        settings.rate_limit_per_minute
        if rate_limit_per_minute is None
        else rate_limit_per_minute
    )
    enabled = (
        settings.rate_limit_enabled
        if rate_limit_enabled is None
        else rate_limit_enabled
    )

    app = FastAPI(
        title="Botburrow Hub",
        description="Agent registry and coordination service",
        version=__version__,
        lifespan=lifespan,
    )

    # Rate limiter first, CORS second: Starlette runs later-added middleware
    # outermost, so CORS headers are also applied to 429 responses.
    app.add_middleware(
        RateLimitMiddleware,
        limit=limit,
        enabled=enabled,
        window_seconds=60,
        prefix=settings.api_prefix,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[
                "Retry-After",
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "X-RateLimit-Reset",
            ],
        )

    app.include_router(agents_router, prefix=settings.api_prefix)
    app.include_router(webhooks_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        """Unprefixed liveness probe for k8s (ADR-007)."""
        return {"status": "ok", "service": "botburrow-hub"}

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
