"""
Database models and migrations for Botburrow Hub.

This module provides the SQLAlchemy models for the Hub database,
including agent definitions with config source tracking.
"""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime
import uuid

import sqlalchemy
from sqlalchemy import select, update, delete, ForeignKey
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy.types import String, DateTime, Boolean, Integer, TIMESTAMP, UUID
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
    api_key_expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, index=True,
        comment="API key expiration timestamp for scheduled rotation"
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
            "api_key_expires_at": self.api_key_expires_at.isoformat() if self.api_key_expires_at else None,
            "last_active_at": self.last_active_at.isoformat() if self.last_active_at else None,
            "karma": self.karma,
            "is_admin": self.is_admin,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ApiKeyHistory(Base):
    """API key history model for rotation tracking with grace period support."""

    __tablename__ = "api_key_history"

    # Primary key
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Foreign key to agents table
    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Old API key hash (SHA256) for authentication during grace period
    old_key_hash: Mapped[str] = mapped_column(
        String, nullable=False, index=True,
        comment="SHA256 hash of old API key"
    )

    # When the key was rotated (new key became active)
    rotated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(),
        comment="Timestamp when the key was rotated"
    )

    # When the old key expires (end of grace period)
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, index=True,
        comment="Timestamp when the old key expires (end of grace period)"
    )

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    def to_dict(self) -> dict:
        """Convert API key history entry to dictionary for API responses."""
        return {
            "id": str(self.id),
            "agent_id": self.agent_id,
            "rotated_at": self.rotated_at.isoformat() if self.rotated_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
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
        api_key_expires_at: Optional[datetime] = None,
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
            api_key_expires_at: Optional API key expiration timestamp

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
            api_key_expires_at=api_key_expires_at,
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

    async def update_api_key(
        self,
        agent_id: str,
        new_api_key_hash: str,
        old_key_hash: str,
        grace_period_expires_at: datetime,
        rotated_at: Optional[datetime] = None,
    ) -> Optional[Agent]:
        """Update an agent's API key and record the old key in history.

        This method performs an atomic transaction-safe operation that:
        1. Creates a history entry for the old API key
        2. Updates the agent's api_key_hash to the new value

        Args:
            agent_id: Agent ID to update
            new_api_key_hash: SHA256 hash of the new API key
            old_key_hash: SHA256 hash of the old API key (for history)
            grace_period_expires_at: When the old key expires (end of grace period)
            rotated_at: Timestamp when the key was rotated (defaults to now)

        Returns:
            Updated Agent instance, or None if agent not found

        Raises:
            ValueError: If old_key_hash doesn't match current agent's api_key_hash
        """
        if rotated_at is None:
            rotated_at = datetime.now()

        # Get the current agent to verify the old key hash
        agent = await self.get_by_id(agent_id)
        if agent is None:
            return None

        # Verify that the old key hash matches
        if agent.api_key_hash != old_key_hash:
            raise ValueError(
                f"Old API key hash mismatch for agent {agent_id}. "
                f"Expected {agent.api_key_hash}, got {old_key_hash}"
            )

        # Create history entry for the old key
        history_repo = ApiKeyHistoryRepository(self.session)
        await history_repo.create(
            agent_id=agent_id,
            old_key_hash=old_key_hash,
            rotated_at=rotated_at,
            expires_at=grace_period_expires_at,
        )

        # Update the agent's API key hash
        agent.api_key_hash = new_api_key_hash
        await self.session.flush()

        return agent


class ApiKeyHistoryRepository:
    """Repository for API key history database operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        agent_id: str,
        old_key_hash: str,
        rotated_at: datetime,
        expires_at: datetime,
    ) -> ApiKeyHistory:
        """Create a new API key history entry when rotating keys.

        Args:
            agent_id: Agent ID (foreign key)
            old_key_hash: SHA256 hash of the old API key
            rotated_at: Timestamp when the key was rotated
            expires_at: Timestamp when the old key expires (end of grace period)

        Returns:
            Created ApiKeyHistory instance
        """
        history_entry = ApiKeyHistory(
            agent_id=agent_id,
            old_key_hash=old_key_hash,
            rotated_at=rotated_at,
            expires_at=expires_at,
        )
        self.session.add(history_entry)
        await self.session.flush()
        return history_entry

    async def get_by_id(self, history_id: str) -> Optional[ApiKeyHistory]:
        """Get API key history entry by ID."""
        stmt = select(ApiKeyHistory).where(ApiKeyHistory.id == history_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_old_key_hash(self, old_key_hash: str) -> Optional[ApiKeyHistory]:
        """Get API key history entry by old key hash."""
        stmt = select(ApiKeyHistory).where(
            ApiKeyHistory.old_key_hash == old_key_hash
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_valid_old_key(
        self, old_key_hash: str, now: Optional[datetime] = None
    ) -> Optional[ApiKeyHistory]:
        """Get valid old key entry that hasn't expired yet.

        Args:
            old_key_hash: SHA256 hash of the old API key
            now: Current time (defaults to now if not provided)

        Returns:
            ApiKeyHistory if key is still valid (within grace period), None otherwise
        """
        if now is None:
            now = datetime.now()

        stmt = select(ApiKeyHistory).where(
            ApiKeyHistory.old_key_hash == old_key_hash,
            ApiKeyHistory.expires_at > now,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_agent(
        self,
        agent_id: str,
        active_only: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ApiKeyHistory]:
        """List API key history entries for an agent.

        Args:
            agent_id: Agent ID
            active_only: If True, only return entries that haven't expired
            offset: Pagination offset
            limit: Maximum number of results

        Returns:
            List of ApiKeyHistory entries
        """
        stmt = (
            select(ApiKeyHistory)
            .where(ApiKeyHistory.agent_id == agent_id)
            .order_by(ApiKeyHistory.rotated_at.desc())
            .offset(offset)
            .limit(limit)
        )

        if active_only:
            stmt = stmt.where(ApiKeyHistory.expires_at > func.now())

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_expired(self, now: Optional[datetime] = None) -> int:
        """Delete expired API key history entries.

        Args:
            now: Current time (defaults to now if not provided)

        Returns:
            Number of entries deleted
        """
        if now is None:
            now = datetime.now()

        stmt = delete(ApiKeyHistory).where(ApiKeyHistory.expires_at <= now)
        result = await self.session.execute(stmt)
        return result.rowcount

    async def delete_by_agent(self, agent_id: str) -> int:
        """Delete all API key history entries for an agent.

        Args:
            agent_id: Agent ID

        Returns:
            Number of entries deleted
        """
        stmt = delete(ApiKeyHistory).where(ApiKeyHistory.agent_id == agent_id)
        result = await self.session.execute(stmt)
        return result.rowcount
