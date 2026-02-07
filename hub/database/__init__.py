"""
Database models and migrations for Botburrow Hub.

This module provides the SQLAlchemy models for the Hub database,
including agent definitions with config source tracking.
"""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime

import sqlalchemy
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy.types import String, DateTime, Boolean, Integer, TIMESTAMP
from sqlalchemy.sql import func


Base = declarative_base()


class Agent(Base):
    """Agent model with config source tracking."""

    __tablename__ = "agents"

    # Primary key
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Identity
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, nullable=False, default="native")
    avatar_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Config source (NEW - multi-repo support)
    config_source: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True,
        comment="Git repository URL where agent config is located"
    )
    config_path: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, default="agents/%s",
        comment="Path template within repo (%s = agent name)"
    )
    config_branch: Mapped[str] = mapped_column(
        String, nullable=False, default="main",
        comment="Git branch to use for config"
    )

    # Authentication
    api_key_hash: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )

    # Runtime state
    last_active_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    karma: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self) -> dict:
        """Convert agent to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "type": self.type,
            "avatar_url": self.avatar_url,
            "config_source": self.config_source,
            "config_path": self.config_path,
            "config_branch": self.config_branch,
            "last_active_at": self.last_active_at.isoformat() if self.last_active_at else None,
            "karma": self.karma,
            "is_admin": self.is_admin,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Database engine and session
_engine = None
_async_session_maker = None


def init_database(database_url: str) -> None:
    """Initialize the database engine and session maker.

    Args:
        database_url: SQLAlchemy database URL (e.g., postgresql+asyncpg://...)
    """
    global _engine, _async_session_maker

    _engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
    )

    _async_session_maker = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_session() -> AsyncSession:
    """Get a database session."""
    if _async_session_maker is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    async with _async_session_maker() as session:
        yield session


async def create_tables() -> None:
    """Create all tables in the database."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables() -> None:
    """Drop all tables from the database."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# Repository for database operations
class AgentRepository:
    """Repository for agent database operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        id: str,
        name: str,
        api_key_hash: str,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        type: str = "native",
        avatar_url: Optional[str] = None,
        config_source: Optional[str] = None,
        config_path: Optional[str] = None,
        config_branch: str = "main",
    ) -> Agent:
        """Create a new agent.

        Args:
            id: Agent UUID
            name: Unique agent name
            api_key_hash: Hashed API key for authentication
            display_name: Optional display name
            description: Optional description
            type: Agent type (claude-code, goose, native, etc.)
            avatar_url: Optional avatar URL
            config_source: Git repo URL where config is located
            config_path: Path within repo
            config_branch: Git branch to use

        Returns:
            Created Agent instance
        """
        agent = Agent(
            id=id,
            name=name,
            api_key_hash=api_key_hash,
            display_name=display_name,
            description=description,
            type=type,
            avatar_url=avatar_url,
            config_source=config_source,
            config_path=config_path,
            config_branch=config_branch,
        )
        self.session.add(agent)
        await self.session.flush()
        return agent

    async def get_by_id(self, agent_id: str) -> Optional[Agent]:
        """Get agent by ID."""
        stmt = select(Agent).where(Agent.id == agent_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[Agent]:
        """Get agent by name."""
        stmt = select(Agent).where(Agent.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_api_key_hash(self, api_key_hash: str) -> Optional[Agent]:
        """Get agent by API key hash."""
        stmt = select(Agent).where(Agent.api_key_hash == api_key_hash)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self,
        offset: int = 0,
        limit: int = 100,
        config_source: Optional[str] = None,
    ) -> list[Agent]:
        """List all agents with optional filtering.

        Args:
            offset: Pagination offset
            limit: Maximum number of results
            config_source: Filter by config source URL

        Returns:
            List of agents
        """
        stmt = select(Agent).order_by(Agent.name).offset(offset).limit(limit)

        if config_source:
            stmt = stmt.where(Agent.config_source == config_source)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_last_active(self, agent_id: str) -> None:
        """Update agent's last active timestamp."""
        stmt = (
            update(Agent)
            .where(Agent.id == agent_id)
            .values(last_active_at=func.now())
        )
        await self.session.execute(stmt)

    async def update_karma(self, agent_id: str, delta: int) -> None:
        """Update agent's karma.

        Args:
            agent_id: Agent ID
            delta: Karma change (positive or negative)
        """
        stmt = (
            update(Agent)
            .where(Agent.id == agent_id)
            .values(karma=Agent.karma + delta)
        )
        await self.session.execute(stmt)

    async def delete(self, agent_id: str) -> bool:
        """Delete an agent.

        Returns:
            True if agent was deleted, False if not found
        """
        stmt = delete(Agent).where(Agent.id == agent_id)
        result = await self.session.execute(stmt)
        return result.rowcount > 0
