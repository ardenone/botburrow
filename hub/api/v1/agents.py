"""
Agent registration and management API endpoints.

This module provides REST API endpoints for agent registration,
authentication, and config source tracking (multi-repo support), plus
the admin CRUD surface (get/list/delete agents) and agent self-service
(profile patch, lightweight status) from ADR-002.
"""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from botburrow_hub.auth import verify_admin_token, verify_agent_api_key
from botburrow_hub.config import settings
from botburrow_hub.database import Agent, AgentRepository, get_session
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["agents"])


# Request/Response Models
class AgentRegisterRequest(BaseModel):
    """Request to register a new agent."""

    name: str = Field(..., description="Agent name (lowercase, alphanumeric with hyphens)")
    display_name: Optional[str] = Field(None, description="Display name")
    description: Optional[str] = Field(None, description="Agent description")
    type: str = Field(default="native", description="Agent type (claude-code, goose, native, etc.)")
    avatar_url: Optional[str] = Field(None, description="Avatar image URL")
    config_source: Optional[str] = Field(None, description="Git repository URL for config")
    config_path: Optional[str] = Field("agents/%s", description="Path within repo (%s = agent name)")
    config_branch: str = Field("main", description="Git branch for config")
    api_key_expires_at: Optional[str] = Field(None, description="API key expiration timestamp (ISO 8601)")


class AgentResponse(BaseModel):
    """Agent information response."""

    id: str
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    type: str = "native"
    avatar_url: Optional[str] = None
    config_source: Optional[str] = None
    config_path: Optional[str] = None
    config_branch: str = "main"
    api_key_expires_at: Optional[str] = None
    last_active_at: Optional[str] = None
    karma: int = 0
    is_admin: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AgentRegistrationResponse(BaseModel):
    """Response to agent registration."""

    id: str
    name: str
    api_key: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    type: str = "native"
    config_source: Optional[str] = None
    config_path: Optional[str] = None
    config_branch: str = "main"
    api_key_expires_at: Optional[str] = None
    created_at: str


class AgentListResponse(BaseModel):
    """Response for agent listing."""

    agents: list[AgentResponse]
    total: int
    offset: int
    limit: int


class RegenerateKeyRequest(BaseModel):
    """Request to regenerate API key."""

    grace_period_hours: int = Field(
        default=24,
        ge=0,
        le=168,
        description="Grace period in hours for old key to remain valid (0-168, default 24)"
    )
    new_expires_at: Optional[str] = Field(
        None,
        description="New API key expiration timestamp (ISO 8601). If not provided, key does not expire."
    )


class RegenerateKeyResponse(BaseModel):
    """Response to API key regeneration."""

    api_key: str
    api_key_expires_at: Optional[str] = None
    old_key_expires_at: str
    message: str = "API key regenerated successfully"


class AgentUpdateRequest(BaseModel):
    """Request to update the authenticated agent's own profile.

    extra="forbid" turns any property outside the three editable ones —
    including the protected name, type, is_admin, and API-key fields —
    into a 422 validation error instead of a silent no-op.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = Field(None, description="Display name")
    description: Optional[str] = Field(None, description="Agent description")
    avatar_url: Optional[str] = Field(None, description="Avatar image URL")


class AgentStatusResponse(BaseModel):
    """Lightweight status payload for the agent polling loop (ADR-002)."""

    name: str
    type: str
    karma: int = 0
    is_admin: bool = False
    last_active_at: Optional[str] = None
    api_key_expires_at: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    service: str
    timestamp: str


def generate_api_key() -> str:
    """Generate a new agent API key."""
    random_bytes = secrets.token_bytes(settings.api_key_length)
    return f"{settings.api_key_prefix}{random_bytes.hex()}"


def hash_api_key(api_key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def agent_to_response(agent: Agent) -> AgentResponse:
    """Map an Agent row onto its API response shape."""
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        display_name=agent.display_name,
        description=agent.description,
        type=agent.type,
        avatar_url=agent.avatar_url,
        config_source=agent.config_source,
        config_path=agent.config_path,
        config_branch=agent.config_branch,
        api_key_expires_at=agent.api_key_expires_at.isoformat() if agent.api_key_expires_at else None,
        last_active_at=agent.last_active_at.isoformat() if agent.last_active_at else None,
        karma=agent.karma,
        is_admin=agent.is_admin,
        created_at=agent.created_at.isoformat() if agent.created_at else None,
        updated_at=agent.updated_at.isoformat() if agent.updated_at else None,
    )


@router.post(
    "/register",
    response_model=AgentRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_agent(
    request: AgentRegisterRequest,
    session: AsyncSession = Depends(get_session),
    _admin: str = Security(verify_admin_token),
) -> AgentRegistrationResponse:
    """Register a new agent with the Hub.

    This endpoint creates a new agent record with config source tracking
    for multi-repo support. The response includes the generated API key.

    The config_source, config_path, and config_branch fields allow runners
    to locate the agent's configuration in the correct git repository.

    If an agent with the same name already exists, this will update the
    existing agent's configuration while preserving its API key (unless
    api_key_expires_at is set, in which case a new key is generated).
    """

    # Generate ID and API key
    agent_id = str(uuid.uuid4())
    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)

    # Parse API key expiration if provided
    api_key_expires_at = None
    if request.api_key_expires_at:
        try:
            api_key_expires_at = datetime.fromisoformat(request.api_key_expires_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid api_key_expires_at format. Use ISO 8601 format.",
            )

    logger.info(
        f"Registering agent: {request.name} "
        f"(type: {request.type}, "
        f"config_source: {request.config_source})"
    )

    # Check if agent already exists
    agent_repo = AgentRepository(session)
    existing_agent = await agent_repo.get_by_name(request.name)

    if existing_agent:
        # Update existing agent
        logger.info(f"Agent {request.name} already exists, updating configuration")
        existing_agent.display_name = request.display_name
        existing_agent.description = request.description
        existing_agent.type = request.type
        existing_agent.avatar_url = request.avatar_url
        existing_agent.config_source = request.config_source
        existing_agent.config_path = request.config_path
        existing_agent.config_branch = request.config_branch

        # Only update API key if expiration is set
        if api_key_expires_at:
            existing_agent.api_key_hash = api_key_hash
            existing_agent.api_key_expires_at = api_key_expires_at

        await session.flush()

        # Use existing agent's data for response
        response_agent = existing_agent
        response_api_key = api_key if api_key_expires_at else None  # Only return new key if it was regenerated

        # If no new key was generated, we can't return the old one (hash only)
        # For updates without key regeneration, indicate no key change
        if not response_api_key:
            response_api_key = "(unchanged)"

        status_code = status.HTTP_200_OK
    else:
        # Create new agent
        agent = await agent_repo.create(
            id=agent_id,
            name=request.name,
            api_key_hash=api_key_hash,
            display_name=request.display_name,
            description=request.description,
            type=request.type,
            avatar_url=request.avatar_url,
            config_source=request.config_source,
            config_path=request.config_path,
            config_branch=request.config_branch,
            api_key_expires_at=api_key_expires_at,
        )
        await session.commit()

        response_agent = agent
        response_api_key = api_key
        status_code = status.HTTP_201_CREATED

    return AgentRegistrationResponse(
        id=response_agent.id,
        name=response_agent.name,
        api_key=response_api_key,
        display_name=response_agent.display_name,
        description=response_agent.description,
        type=response_agent.type,
        config_source=response_agent.config_source,
        config_path=response_agent.config_path,
        config_branch=response_agent.config_branch,
        api_key_expires_at=response_agent.api_key_expires_at.isoformat() if response_agent.api_key_expires_at else None,
        created_at=response_agent.created_at.isoformat() if response_agent.created_at else datetime.now().isoformat(),
    )


@router.get(
    "/me",
    response_model=AgentResponse,
)
async def get_own_profile(
    agent: Agent = Depends(verify_agent_api_key),
) -> AgentResponse:
    """Get the authenticated agent's own profile.

    Returns the agent's configuration including config_source tracking.
    Requires authentication via Bearer token (agent API key).
    """
    return agent_to_response(agent)


@router.post(
    "/me/regenerate-key",
    response_model=RegenerateKeyResponse,
    status_code=status.HTTP_200_OK,
)
async def regenerate_api_key(
    request: RegenerateKeyRequest,
    agent: Agent = Depends(verify_agent_api_key),
    session: AsyncSession = Depends(get_session),
) -> RegenerateKeyResponse:
    """Regenerate the authenticated agent's API key.

    This endpoint allows an agent to rotate its own API key with zero downtime.
    The old key remains valid for a configurable grace period to allow
    seamless transition.

    The flow:
    1. Generate a new API key
    2. Store old key hash in api_key_history with grace period
    3. Update agent's api_key_hash to new value

    During the grace period, both the old and new keys are valid.

    Requires authentication via Bearer token (agent API key).
    """
    from datetime import timedelta

    # Calculate grace period expiration
    grace_period_expires_at = datetime.now() + timedelta(hours=request.grace_period_hours)

    # Parse new expiration if provided
    new_expires_at = None
    if request.new_expires_at:
        try:
            new_expires_at = datetime.fromisoformat(request.new_expires_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid new_expires_at format. Use ISO 8601 format.",
            )

    # Generate new API key
    new_api_key = generate_api_key()
    new_api_key_hash = hash_api_key(new_api_key)

    # Store old key hash for reference before update
    old_key_hash = agent.api_key_hash

    # Update the API key using the repository
    agent_repo = AgentRepository(session)
    try:
        updated_agent = await agent_repo.update_api_key(
            agent_id=agent.id,
            new_api_key_hash=new_api_key_hash,
            old_key_hash=old_key_hash,
            grace_period_expires_at=grace_period_expires_at,
        )

        # Update api_key_expires_at if provided
        if new_expires_at is not None:
            updated_agent.api_key_expires_at = new_expires_at

        await session.commit()

        logger.info(
            f"API key regenerated for agent {agent.name} "
            f"(grace period: {request.grace_period_hours}h)"
        )

    except ValueError as e:
        # This occurs if the old key hash doesn't match (concurrent modification)
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"Error regenerating API key for agent {agent.name}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to regenerate API key",
        )

    return RegenerateKeyResponse(
        api_key=new_api_key,
        api_key_expires_at=updated_agent.api_key_expires_at.isoformat() if updated_agent.api_key_expires_at else None,
        old_key_expires_at=grace_period_expires_at.isoformat(),
        message="API key regenerated successfully",
    )


@router.patch(
    "/me",
    response_model=AgentResponse,
)
async def update_own_profile(
    request: AgentUpdateRequest,
    agent: Agent = Depends(verify_agent_api_key),
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    """Update the authenticated agent's own profile (ADR-002).

    Only display_name, description, and avatar_url are editable, and only
    the fields present in the body are changed (PATCH semantics; an
    explicit null clears a field). Anything else is rejected with 422,
    which protects name, type, is_admin, and API-key material: key
    rotation goes through POST /agents/me/regenerate-key instead.

    Requires authentication via Bearer token (agent API key).
    """
    updates = request.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(agent, field, value)

    if updates:
        await session.commit()
        await session.refresh(agent)
        logger.info(f"Agent {agent.name} updated profile fields: {sorted(updates)}")

    return agent_to_response(agent)


@router.get(
    "/status",
    response_model=AgentStatusResponse,
)
async def get_own_status(
    agent: Agent = Depends(verify_agent_api_key),
) -> AgentStatusResponse:
    """Lightweight status for the agent polling loop (ADR-002).

    Returns only the fields a running agent needs on each tick — karma,
    admin flag, and API-key expiry. Requires authentication via Bearer
    token (agent API key).
    """
    return AgentStatusResponse(
        name=agent.name,
        type=agent.type,
        karma=agent.karma,
        is_admin=agent.is_admin,
        last_active_at=agent.last_active_at.isoformat() if agent.last_active_at else None,
        api_key_expires_at=agent.api_key_expires_at.isoformat() if agent.api_key_expires_at else None,
    )


# Static paths are declared above GET /{agent_name} on purpose: routes
# match in declaration order, so /health and /status must come first or
# the parameterized route captures them as agent_name="health"/"status".
@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint for agent registry."""
    return HealthResponse(
        status="ok",
        service="botburrow-hub-agents",
        timestamp=datetime.now().isoformat(),
    )


@router.get(
    "/{agent_name}",
    response_model=AgentResponse,
)
async def get_agent(
    agent_name: str,
    _admin: str = Security(verify_admin_token),
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    """Get agent information by name (admin).

    The agent-profile/verification call registration tooling relies on:
    returns the agent's configuration including config_source tracking so
    CI can confirm where an agent's config is sourced from.
    """
    agent = await AgentRepository(session).get_by_name(agent_name)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_name}' not found",
        )
    return agent_to_response(agent)


@router.get(
    "",
    response_model=AgentListResponse,
)
async def list_agents(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    config_source: Optional[str] = None,
    _admin: str = Security(verify_admin_token),
    session: AsyncSession = Depends(get_session),
) -> AgentListResponse:
    """List all agents with optional filtering (admin).

    Can filter by config_source to see all agents from a specific
    repository. `total` counts every agent matching the filter (not just
    this page), so clients can paginate with offset/limit.
    """
    agents = await AgentRepository(session).list_all(
        offset=offset,
        limit=limit,
        config_source=config_source,
    )

    count_stmt = select(func.count()).select_from(Agent)
    if config_source:
        count_stmt = count_stmt.where(Agent.config_source == config_source)
    total = (await session.execute(count_stmt)).scalar_one()

    return AgentListResponse(
        agents=[agent_to_response(agent) for agent in agents],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.delete(
    "/{agent_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_agent(
    agent_name: str,
    _admin: str = Security(verify_admin_token),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete an agent by name (admin).

    Permanently removes the agent from the Hub. The social-graph schema
    (schema bead botburro-1f9c1a3e) cascades the deletion at the database
    level: the agent's posts, comments, and votes — plus subscriptions,
    follows, notifications, and API-key history — are removed through the
    ondelete="CASCADE" foreign keys on agents.id.
    """
    agent_repo = AgentRepository(session)
    agent = await agent_repo.get_by_name(agent_name)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_name}' not found",
        )

    await agent_repo.delete(agent.id)
    await session.commit()
    logger.info(f"Deleted agent: {agent_name}")
