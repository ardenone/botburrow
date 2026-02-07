"""
Agent registration and management API endpoints.

This module provides REST API endpoints for agent registration,
authentication, and config source tracking (multi-repo support).
"""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from botburrow_hub.auth import verify_admin_token, verify_agent_api_key
from botburrow_hub.config import settings
from botburrow_hub.database import AgentRepository

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
    created_at: str


class AgentListResponse(BaseModel):
    """Response for agent listing."""

    agents: list[AgentResponse]
    total: int
    offset: int
    limit: int


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


@router.post(
    "/register",
    response_model=AgentRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_agent(
    request: AgentRegisterRequest,
    _admin: str = Security(verify_admin_token),
) -> AgentRegistrationResponse:
    """Register a new agent with the Hub.

    This endpoint creates a new agent record with config source tracking
    for multi-repo support. The response includes the generated API key.

    The config_source, config_path, and config_branch fields allow runners
    to locate the agent's configuration in the correct git repository.
    """
    # TODO: Implement with async database session
    # For now, return a mock response
    agent_id = str(uuid.uuid4())
    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)

    logger.info(
        f"Registering agent: {request.name} "
        f"(type: {request.type}, "
        f"config_source: {request.config_source})"
    )

    # TODO: Store in database via AgentRepository
    # agent = await agent_repo.create(
    #     id=agent_id,
    #     name=request.name,
    #     api_key_hash=api_key_hash,
    #     display_name=request.display_name,
    #     description=request.description,
    #     type=request.type,
    #     avatar_url=request.avatar_url,
    #     config_source=request.config_source,
    #     config_path=request.config_path,
    #     config_branch=request.config_branch,
    # )

    return AgentRegistrationResponse(
        id=agent_id,
        name=request.name,
        api_key=api_key,
        display_name=request.display_name,
        description=request.description,
        type=request.type,
        config_source=request.config_source,
        config_path=request.config_path % request.name if "%s" in (request.config_path or "") else request.config_path,
        config_branch=request.config_branch,
        created_at=datetime.now().isoformat(),
    )


@router.get(
    "/{agent_name}",
    response_model=AgentResponse,
)
async def get_agent(
    agent_name: str,
    _admin: str = Security(verify_admin_token),
) -> AgentResponse:
    """Get agent information by name.

    Returns the agent's configuration including config_source tracking.
    """
    # TODO: Implement with async database session
    # agent = await agent_repo.get_by_name(agent_name)
    # if not agent:
    #     raise HTTPException(
    #         status_code=status.HTTP_404_NOT_FOUND,
    #         detail=f"Agent '{agent_name}' not found"
    #     )

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Agent retrieval not yet implemented",
    )


@router.get(
    "",
    response_model=AgentListResponse,
)
async def list_agents(
    offset: int = 0,
    limit: int = 100,
    config_source: Optional[str] = None,
    _admin: str = Security(verify_admin_token),
) -> AgentListResponse:
    """List all agents with optional filtering.

    Can filter by config_source to see all agents from a specific repository.
    """
    # TODO: Implement with async database session
    # agents = await agent_repo.list_all(
    #     offset=offset,
    #     limit=limit,
    #     config_source=config_source,
    # )

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Agent listing not yet implemented",
    )


@router.delete(
    "/{agent_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_agent(
    agent_name: str,
    _admin: str = Security(verify_admin_token),
) -> None:
    """Delete an agent by name.

    Permanently removes the agent from the Hub.
    """
    # TODO: Implement with async database session
    # success = await agent_repo.delete_by_name(agent_name)
    # if not success:
    #     raise HTTPException(
    #         status_code=status.HTTP_404_NOT_FOUND,
    #         detail=f"Agent '{agent_name}' not found"
    #     )

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Agent deletion not yet implemented",
    )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint for agent registry."""
    return HealthResponse(
        status="ok",
        service="botburrow-hub-agents",
        timestamp=datetime.now().isoformat(),
    )
