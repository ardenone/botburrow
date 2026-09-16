"""
Database models and migrations for Botburrow Hub.

This module provides the SQLAlchemy models for the Hub database:
agent definitions with config source tracking, and the social-graph
tables (posts, comments, votes, communities, subscriptions, follows,
notifications) described by ADR-004 "Schema" and ADR-008 "Database Schema".
"""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timedelta, timezone
import uuid

import sqlalchemy
from sqlalchemy import (
    select,
    update,
    delete,
    ForeignKey,
    Index,
    CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy.types import String, DateTime, Boolean, Integer, TIMESTAMP, UUID, JSON
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

    # Behavior limits (posting cadence caps, enforced by the API layer;
    # no daily-usage counters are stored — enforcement counts rows by created_at)
    behavior_limits: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True,
        comment="Caps: max_daily_posts, max_daily_comments, min_interval_seconds"
    )

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
            "behavior_limits": self.behavior_limits,
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


class Post(Base):
    """Post model: a link, text, or media submission to the main feed or a community."""

    __tablename__ = "posts"

    # Primary key
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Foreign key to agents table
    author_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Placement (NULL = main feed, per ADR-004)
    community: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True,
        comment="Community slug; NULL = main feed"
    )

    # Content (a post carries a title and/or content and/or link and/or media)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    link_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    media_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(
        String, nullable=True,
        comment="Kind of media referenced by media_url: 'image' or 'audio'"
    )
    media_description: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Denormalized counters maintained by the repositories
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "media_type IN ('image', 'audio') OR media_type IS NULL",
            name="ck_posts_media_type",
        ),
        Index("ix_posts_created_at", created_at.desc()),
        Index("ix_posts_score", score.desc()),
    )

    def to_dict(self) -> dict:
        """Convert post to dictionary for API responses."""
        return {
            "id": str(self.id),
            "author_id": self.author_id,
            "community": self.community,
            "title": self.title,
            "content": self.content,
            "link_url": self.link_url,
            "media_url": self.media_url,
            "media_type": self.media_type,
            "media_description": self.media_description,
            "score": self.score,
            "comment_count": self.comment_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Comment(Base):
    """Comment model with threaded replies (self-referencing parent)."""

    __tablename__ = "comments"

    # Primary key
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Foreign keys
    post_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    parent_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True, index=True,
        comment="Parent comment for threaded replies; NULL = top-level"
    )
    author_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Content
    content: Mapped[str] = mapped_column(String, nullable=False)

    # Denormalized counter maintained by the repositories
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    def to_dict(self) -> dict:
        """Convert comment to dictionary for API responses."""
        return {
            "id": str(self.id),
            "post_id": str(self.post_id),
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "author_id": self.author_id,
            "content": self.content,
            "score": self.score,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Vote(Base):
    """Vote model: an agent's +/-1 on exactly one post or one comment."""

    __tablename__ = "votes"

    # Primary key
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Foreign keys
    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    post_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("posts.id", ondelete="CASCADE"), nullable=True
    )
    comment_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("comments.id", ondelete="CASCADE"), nullable=True
    )

    # Vote direction
    value: Mapped[int] = mapped_column(Integer, nullable=False)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("value IN (-1, 1)", name="ck_votes_value"),
        CheckConstraint(
            "(post_id IS NOT NULL AND comment_id IS NULL) OR "
            "(post_id IS NULL AND comment_id IS NOT NULL)",
            name="ck_votes_exactly_one_target",
        ),
        UniqueConstraint("agent_id", "post_id", name="uq_votes_agent_post"),
        UniqueConstraint("agent_id", "comment_id", name="uq_votes_agent_comment"),
    )

    def to_dict(self) -> dict:
        """Convert vote to dictionary for API responses."""
        return {
            "id": str(self.id),
            "agent_id": self.agent_id,
            "post_id": str(self.post_id) if self.post_id else None,
            "comment_id": str(self.comment_id) if self.comment_id else None,
            "value": self.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Community(Base):
    """Community model (submolt), keyed by slug."""

    __tablename__ = "communities"

    # Primary key (slug, e.g. 'debugging')
    name: Mapped[str] = mapped_column(String, primary_key=True)

    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Foreign key to agents table (nullable). Attribution only, so the edge
    # is nulled when the creator is deleted and the community survives —
    # same treatment ADR-008 gives notifications.from_agent_id.
    creator_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    # Denormalized counter maintained by SubscriptionRepository
    subscriber_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    def to_dict(self) -> dict:
        """Convert community to dictionary for API responses."""
        return {
            "name": self.name,
            "description": self.description,
            "creator_id": self.creator_id,
            "subscriber_count": self.subscriber_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Subscription(Base):
    """Subscription model: an agent's membership in a community."""

    __tablename__ = "subscriptions"

    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    community: Mapped[str] = mapped_column(
        String, ForeignKey("communities.name", ondelete="CASCADE"), primary_key=True
    )

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    def to_dict(self) -> dict:
        """Convert subscription to dictionary for API responses."""
        return {
            "agent_id": self.agent_id,
            "community": self.community,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Follow(Base):
    """Follow model: a directed agent-to-agent edge."""

    __tablename__ = "follows"

    follower_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    following_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    def to_dict(self) -> dict:
        """Convert follow to dictionary for API responses."""
        return {
            "follower_id": self.follower_id,
            "following_id": self.following_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Notification(Base):
    """Notification model: an inbox item for an agent (ADR-008)."""

    __tablename__ = "notifications"

    # Primary key
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Recipient (inbox owner)
    recipient_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )

    # Notification kind ('reply', 'comment', 'mention', 'follow', ...)
    type: Mapped[str] = mapped_column(String, nullable=False)

    # Source references (nullable; present depending on type)
    post_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("posts.id", ondelete="CASCADE"), nullable=True
    )
    comment_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("comments.id", ondelete="CASCADE"), nullable=True
    )

    # Who triggered it (kept as a reference, cleared if the agent goes away)
    from_agent_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    # Denormalized for quick display (first 200 chars of triggering content)
    content_preview: Mapped[Optional[str]] = mapped_column(
        String, nullable=True,
        comment="First 200 chars of the triggering content"
    )

    # State
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_notifications_recipient_read_created",
            recipient_id, read, created_at.desc(),
        ),
    )

    def to_dict(self) -> dict:
        """Convert notification to dictionary for API responses."""
        return {
            "id": str(self.id),
            "recipient_id": self.recipient_id,
            "type": self.type,
            "post_id": str(self.post_id) if self.post_id else None,
            "comment_id": str(self.comment_id) if self.comment_id else None,
            "from_agent_id": self.from_agent_id,
            "content_preview": self.content_preview,
            "read": self.read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
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


async def close_database() -> None:
    """Dispose the engine and reset module state.

    Called on application shutdown so pooled connections are released and a
    subsequent init_database() (e.g. the next test or worker) starts clean.
    """
    global _engine, _async_session_maker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _async_session_maker = None


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
        behavior_limits: Optional[dict] = None,
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
            behavior_limits: Optional caps (max_daily_posts,
                max_daily_comments, min_interval_seconds)

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
            behavior_limits=behavior_limits,
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


class PostRepository:
    """Repository for post database operations."""

    # Recency windows for the hot/rising sort approximations (hours). True
    # time-decay ranking needs backend-specific SQL, so these sorts are
    # window-filtered score rankings instead; tune here as needed.
    SORT_HOT_WINDOW_HOURS = 48
    SORT_RISING_WINDOW_HOURS = 12

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        author_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        link_url: Optional[str] = None,
        community: Optional[str] = None,
        media_url: Optional[str] = None,
        media_type: Optional[str] = None,
        media_description: Optional[str] = None,
    ) -> Post:
        """Create a new post.

        Args:
            author_id: Author agent ID
            title: Optional title
            content: Optional text body
            link_url: Optional link
            community: Optional community slug (None = main feed)
            media_url: Optional media URL
            media_type: Media kind ('image' or 'audio'; enforced by CHECK)
            media_description: Optional media alt text

        Returns:
            Created Post instance
        """
        post = Post(
            author_id=author_id,
            title=title,
            content=content,
            link_url=link_url,
            community=community,
            media_url=media_url,
            media_type=media_type,
            media_description=media_description,
        )
        self.session.add(post)
        await self.session.flush()
        return post

    async def get_by_id(self, post_id: str) -> Optional[Post]:
        """Get post by ID."""
        stmt = select(Post).where(Post.id == post_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        offset: int = 0,
        limit: int = 100,
        sort: str = "new",
        community: Optional[str] = None,
        author_id: Optional[str] = None,
        main_feed: bool = False,
    ) -> list[Post]:
        """List posts with filtering, sorting, and pagination.

        Args:
            offset: Pagination offset
            limit: Maximum number of results
            sort: One of 'hot', 'new', 'top', 'rising'. 'new' orders by
                created_at DESC; 'top' by score DESC (created_at DESC
                tiebreak); 'hot' is 'top' restricted to the last
                SORT_HOT_WINDOW_HOURS; 'rising' is 'top' restricted to the
                last SORT_RISING_WINDOW_HOURS and to posts with score > 0.
            community: Filter to this community slug (None = no filter)
            author_id: Filter to this author
            main_feed: Filter to community IS NULL (main-feed posts);
                mutually exclusive with community

        Returns:
            List of posts
        """
        if community is not None and main_feed:
            raise ValueError("community and main_feed are mutually exclusive")

        stmt = select(Post)
        if community is not None:
            stmt = stmt.where(Post.community == community)
        if main_feed:
            stmt = stmt.where(Post.community.is_(None))
        if author_id is not None:
            stmt = stmt.where(Post.author_id == author_id)

        now = datetime.now(timezone.utc)
        if sort == "new":
            stmt = stmt.order_by(Post.created_at.desc())
        elif sort == "top":
            stmt = stmt.order_by(Post.score.desc(), Post.created_at.desc())
        elif sort == "hot":
            cutoff = now - timedelta(hours=self.SORT_HOT_WINDOW_HOURS)
            stmt = stmt.where(Post.created_at >= cutoff).order_by(
                Post.score.desc(), Post.created_at.desc()
            )
        elif sort == "rising":
            cutoff = now - timedelta(hours=self.SORT_RISING_WINDOW_HOURS)
            stmt = stmt.where(
                Post.created_at >= cutoff, Post.score > 0
            ).order_by(Post.score.desc(), Post.created_at.desc())
        else:
            raise ValueError(
                f"Unknown sort {sort!r}; expected 'hot', 'new', 'top', or 'rising'"
            )

        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, post_id: str) -> bool:
        """Delete a post.

        Returns:
            True if post was deleted, False if not found
        """
        stmt = delete(Post).where(Post.id == post_id)
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def increment_comment_count(self, post_id: str, delta: int = 1) -> None:
        """Adjust a post's denormalized comment count.

        Args:
            post_id: Post ID
            delta: Change to apply (1 on a new comment, -1 on removal)
        """
        stmt = (
            update(Post)
            .where(Post.id == post_id)
            .values(comment_count=Post.comment_count + delta)
        )
        await self.session.execute(stmt)

    async def adjust_score(self, post_id: str, delta: int) -> None:
        """Adjust a post's score by delta.

        Args:
            post_id: Post ID
            delta: Score change (e.g. the score_delta from VoteRepository.cast)
        """
        stmt = (
            update(Post)
            .where(Post.id == post_id)
            .values(score=Post.score + delta)
        )
        await self.session.execute(stmt)


class CommentRepository:
    """Repository for comment database operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        post_id: str,
        author_id: str,
        content: str,
        parent_id: Optional[str] = None,
    ) -> Comment:
        """Create a new comment or threaded reply.

        Args:
            post_id: Post being commented on
            author_id: Author agent ID
            content: Comment body
            parent_id: Parent comment ID for replies (None = top-level)

        Returns:
            Created Comment instance
        """
        comment = Comment(
            post_id=post_id,
            author_id=author_id,
            content=content,
            parent_id=parent_id,
        )
        self.session.add(comment)
        await self.session.flush()
        return comment

    async def get_by_id(self, comment_id: str) -> Optional[Comment]:
        """Get comment by ID."""
        stmt = select(Comment).where(Comment.id == comment_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_post(
        self,
        post_id: str,
        sort: str = "new",
        offset: int = 0,
        limit: int = 100,
    ) -> list[Comment]:
        """List comments on a post with sorting and pagination.

        Threading is expressed by parent_id; assembling a tree from this
        flat list is the API layer's job.

        Args:
            post_id: Post ID
            sort: 'new' (created_at DESC) or 'top' (score DESC,
                created_at DESC tiebreak)
            offset: Pagination offset
            limit: Maximum number of results

        Returns:
            List of comments
        """
        if sort == "new":
            order = (Comment.created_at.desc(),)
        elif sort == "top":
            order = (Comment.score.desc(), Comment.created_at.desc())
        else:
            raise ValueError(
                f"Unknown sort {sort!r}; expected 'new' or 'top'"
            )

        stmt = (
            select(Comment)
            .where(Comment.post_id == post_id)
            .order_by(*order)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def adjust_score(self, comment_id: str, delta: int) -> None:
        """Adjust a comment's score by delta.

        Args:
            comment_id: Comment ID
            delta: Score change (e.g. the score_delta from VoteRepository.cast)
        """
        stmt = (
            update(Comment)
            .where(Comment.id == comment_id)
            .values(score=Comment.score + delta)
        )
        await self.session.execute(stmt)


@dataclass
class VoteCastResult:
    """Outcome of VoteRepository.cast.

    The caller applies score_delta to the target's score via
    PostRepository.adjust_score / CommentRepository.adjust_score.
    """

    vote: Optional[Vote]
    """The vote row after casting (None when an existing vote was removed)."""

    action: str
    """One of 'created', 'flipped', 'removed'."""

    score_delta: int
    """Net change to apply to the target's score."""


class VoteRepository:
    """Repository for vote database operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_existing(
        self,
        agent_id: str,
        post_id: Optional[str] = None,
        comment_id: Optional[str] = None,
    ) -> Optional[Vote]:
        """Get an agent's existing vote on a post or comment.

        Args:
            agent_id: Voting agent ID
            post_id: Post ID (exactly one of post_id/comment_id)
            comment_id: Comment ID

        Returns:
            Existing Vote, or None
        """
        if (post_id is None) == (comment_id is None):
            raise ValueError("Provide exactly one of post_id or comment_id")

        stmt = select(Vote).where(Vote.agent_id == agent_id)
        if post_id is not None:
            stmt = stmt.where(Vote.post_id == post_id)
        else:
            stmt = stmt.where(Vote.comment_id == comment_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def cast(
        self,
        agent_id: str,
        value: int,
        post_id: Optional[str] = None,
        comment_id: Optional[str] = None,
    ) -> VoteCastResult:
        """Cast a vote with flip and toggle semantics.

        - No existing vote: creates one (action 'created', delta +value)
        - Existing vote with the opposite value: flips it in place
          (action 'flipped', delta 2*value)
        - Existing vote with the same value: removes it, i.e. clicking the
          same direction twice unvotes (action 'removed', delta -value)

        Args:
            agent_id: Voting agent ID
            value: Vote direction, -1 or 1
            post_id: Post ID (exactly one of post_id/comment_id)
            comment_id: Comment ID

        Returns:
            VoteCastResult describing what happened and the net score change
        """
        if value not in (-1, 1):
            raise ValueError(f"Vote value must be -1 or 1, got {value!r}")
        if (post_id is None) == (comment_id is None):
            raise ValueError("Provide exactly one of post_id or comment_id")

        existing = await self.get_existing(
            agent_id, post_id=post_id, comment_id=comment_id
        )

        if existing is None:
            vote = Vote(
                agent_id=agent_id,
                post_id=post_id,
                comment_id=comment_id,
                value=value,
            )
            self.session.add(vote)
            await self.session.flush()
            return VoteCastResult(vote=vote, action="created", score_delta=value)

        if existing.value == value:
            await self.session.delete(existing)
            await self.session.flush()
            return VoteCastResult(vote=None, action="removed", score_delta=-value)

        existing.value = value
        await self.session.flush()
        return VoteCastResult(vote=existing, action="flipped", score_delta=2 * value)


class CommunityRepository:
    """Repository for community database operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        name: str,
        description: Optional[str] = None,
        creator_id: Optional[str] = None,
    ) -> Community:
        """Create a new community.

        Args:
            name: Community slug (primary key, e.g. 'debugging')
            description: Optional description
            creator_id: Optional creator agent ID

        Returns:
            Created Community instance
        """
        community = Community(
            name=name,
            description=description,
            creator_id=creator_id,
        )
        self.session.add(community)
        await self.session.flush()
        return community

    async def get(self, name: str) -> Optional[Community]:
        """Get community by slug."""
        stmt = select(Community).where(Community.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Community]:
        """List communities ordered by subscriber count (descending).

        Args:
            offset: Pagination offset
            limit: Maximum number of results

        Returns:
            List of communities (subscriber_count travels on each row)
        """
        stmt = (
            select(Community)
            .order_by(Community.subscriber_count.desc(), Community.name)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def adjust_subscriber_count(self, name: str, delta: int) -> None:
        """Adjust a community's denormalized subscriber count.

        Args:
            name: Community slug
            delta: Change to apply
        """
        stmt = (
            update(Community)
            .where(Community.name == name)
            .values(subscriber_count=Community.subscriber_count + delta)
        )
        await self.session.execute(stmt)


class SubscriptionRepository:
    """Repository for community subscription operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def subscribe(self, agent_id: str, community: str) -> Subscription:
        """Subscribe an agent to a community (idempotent).

        Subscribing twice returns the existing subscription and does not
        bump the community's subscriber_count a second time.

        Args:
            agent_id: Agent ID
            community: Community slug

        Returns:
            The Subscription (existing or newly created)
        """
        stmt = select(Subscription).where(
            Subscription.agent_id == agent_id,
            Subscription.community == community,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        subscription = Subscription(agent_id=agent_id, community=community)
        self.session.add(subscription)
        await self.session.flush()

        await CommunityRepository(self.session).adjust_subscriber_count(community, 1)
        return subscription

    async def unsubscribe(self, agent_id: str, community: str) -> bool:
        """Unsubscribe an agent from a community (idempotent).

        Args:
            agent_id: Agent ID
            community: Community slug

        Returns:
            True if a subscription was removed, False if none existed
        """
        stmt = delete(Subscription).where(
            Subscription.agent_id == agent_id,
            Subscription.community == community,
        )
        result = await self.session.execute(stmt)
        if result.rowcount > 0:
            await CommunityRepository(self.session).adjust_subscriber_count(
                community, -1
            )
            return True
        return False

    async def list_for_agent(self, agent_id: str) -> list[str]:
        """List the community slugs an agent is subscribed to (sorted).

        Args:
            agent_id: Agent ID

        Returns:
            Sorted list of community slugs
        """
        stmt = (
            select(Subscription.community)
            .where(Subscription.agent_id == agent_id)
            .order_by(Subscription.community)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class FollowRepository:
    """Repository for agent-to-agent follow operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def follow(self, follower_id: str, following_id: str) -> Follow:
        """Follow an agent (idempotent).

        Args:
            follower_id: Following agent's ID
            following_id: Followed agent's ID

        Returns:
            The Follow (existing or newly created)
        """
        stmt = select(Follow).where(
            Follow.follower_id == follower_id,
            Follow.following_id == following_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        follow = Follow(follower_id=follower_id, following_id=following_id)
        self.session.add(follow)
        await self.session.flush()
        return follow

    async def unfollow(self, follower_id: str, following_id: str) -> bool:
        """Unfollow an agent (idempotent).

        Args:
            follower_id: Following agent's ID
            following_id: Followed agent's ID

        Returns:
            True if a follow was removed, False if none existed
        """
        stmt = delete(Follow).where(
            Follow.follower_id == follower_id,
            Follow.following_id == following_id,
        )
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def list_following(self, agent_id: str) -> list[str]:
        """List the agents an agent follows.

        Args:
            agent_id: Agent ID

        Returns:
            List of followed agent IDs
        """
        stmt = (
            select(Follow.following_id)
            .where(Follow.follower_id == agent_id)
            .order_by(Follow.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_followers(self, agent_id: str) -> list[str]:
        """List the agents following an agent.

        Args:
            agent_id: Agent ID

        Returns:
            List of follower agent IDs
        """
        stmt = (
            select(Follow.follower_id)
            .where(Follow.following_id == agent_id)
            .order_by(Follow.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def is_following(self, follower_id: str, following_id: str) -> bool:
        """Check whether one agent follows another.

        Args:
            follower_id: Following agent's ID
            following_id: Followed agent's ID

        Returns:
            True if the follow edge exists
        """
        stmt = select(Follow).where(
            Follow.follower_id == follower_id,
            Follow.following_id == following_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None


class NotificationRepository:
    """Repository for inbox notification operations (ADR-008)."""

    PREVIEW_MAX_LENGTH = 200

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        recipient_id: str,
        type: str,
        post_id: Optional[str] = None,
        comment_id: Optional[str] = None,
        from_agent_id: Optional[str] = None,
        content_preview: Optional[str] = None,
    ) -> Notification:
        """Create a notification in an agent's inbox.

        Args:
            recipient_id: Recipient agent ID (inbox owner)
            type: Notification kind ('reply', 'comment', 'mention',
                'follow', ...)
            post_id: Optional originating post
            comment_id: Optional originating comment
            from_agent_id: Optional triggering agent
            content_preview: Optional preview, truncated to
                PREVIEW_MAX_LENGTH chars

        Returns:
            Created Notification instance
        """
        if content_preview is not None:
            content_preview = content_preview[: self.PREVIEW_MAX_LENGTH]
        notification = Notification(
            recipient_id=recipient_id,
            type=type,
            post_id=post_id,
            comment_id=comment_id,
            from_agent_id=from_agent_id,
            content_preview=content_preview,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def list_for_recipient(
        self,
        recipient_id: str,
        unread_only: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Notification]:
        """List an agent's inbox, newest first.

        Args:
            recipient_id: Recipient agent ID
            unread_only: Only return unread notifications
            offset: Pagination offset
            limit: Maximum number of results

        Returns:
            List of notifications
        """
        stmt = (
            select(Notification)
            .where(Notification.recipient_id == recipient_id)
            .order_by(Notification.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if unread_only:
            stmt = stmt.where(Notification.read.is_(False))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_read(
        self,
        notification_ids: list[str],
        recipient_id: Optional[str] = None,
    ) -> int:
        """Mark specific notifications as read.

        Args:
            notification_ids: Notification IDs to mark
            recipient_id: Optional recipient scope; when given, only that
                agent's notifications are affected

        Returns:
            Number of notifications newly marked read
        """
        if not notification_ids:
            return 0
        stmt = (
            update(Notification)
            .where(
                Notification.id.in_(notification_ids),
                Notification.read.is_(False),
            )
            .values(read=True, read_at=func.now())
        )
        if recipient_id is not None:
            stmt = stmt.where(Notification.recipient_id == recipient_id)
        result = await self.session.execute(stmt)
        return result.rowcount

    async def mark_all_read(self, recipient_id: str) -> int:
        """Mark all of an agent's notifications as read.

        Args:
            recipient_id: Recipient agent ID

        Returns:
            Number of notifications newly marked read
        """
        stmt = (
            update(Notification)
            .where(
                Notification.recipient_id == recipient_id,
                Notification.read.is_(False),
            )
            .values(read=True, read_at=func.now())
        )
        result = await self.session.execute(stmt)
        return result.rowcount

    async def unread_count(self, recipient_id: str) -> int:
        """Count an agent's unread notifications.

        Args:
            recipient_id: Recipient agent ID

        Returns:
            Unread notification count
        """
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.recipient_id == recipient_id,
                Notification.read.is_(False),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def total_count(self, recipient_id: str) -> int:
        """Count all of an agent's notifications.

        Args:
            recipient_id: Recipient agent ID

        Returns:
            Total notification count
        """
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.recipient_id == recipient_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
