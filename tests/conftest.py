"""
Shared fixtures for the hub database schema/repository test suite.

See tests/README.md for why this suite lives in a top-level tests/
directory and why it runs on aiosqlite.
"""

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_hub_as_botburrow_hub() -> None:
    """Make the hub/ package importable as `botburrow_hub`.

    hub/__init__.py imports itself under the name botburrow_hub, but the
    package is not installed under that name until the app-assembly bead
    lands pyproject.toml. Loading it via spec under the canonical name (and
    pre-registering it in sys.modules before its __init__ runs) makes
    `import botburrow_hub.*` work straight from the checkout. The unaliased
    name `hub` is deliberately NOT registered: a second module object for
    the same files would create a second Base/metadata and silently split
    the schema in two.
    """
    if "botburrow_hub" in sys.modules:
        return
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    spec = importlib.util.spec_from_file_location(
        "botburrow_hub",
        REPO_ROOT / "hub" / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT / "hub")],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules["botburrow_hub"] = package
    spec.loader.exec_module(package)


_load_hub_as_botburrow_hub()

from botburrow_hub.database import Agent, AgentRepository, Base  # noqa: E402


@pytest_asyncio.fixture
async def engine():
    """Fresh in-memory SQLite database per test, with FK enforcement on.

    SQLite does not enforce foreign keys unless PRAGMA foreign_keys=ON is
    set outside a transaction; StaticPool pins the whole test to a single
    connection, so setting it once before create_all covers every later
    session and lets ON DELETE CASCADE / SET NULL behave like PostgreSQL.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.connect() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine):
    """AsyncSession bound to the per-test engine."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@pytest_asyncio.fixture
async def make_agent(session):
    """Factory creating an Agent row with unique defaults."""

    async def _make(name: str | None = None, **kwargs) -> Agent:
        repo = AgentRepository(session)
        return await repo.create(
            id=str(uuid.uuid4()),
            name=name or f"agent-{uuid.uuid4().hex[:12]}",
            api_key_hash=uuid.uuid4().hex,
            **kwargs,
        )

    return _make
