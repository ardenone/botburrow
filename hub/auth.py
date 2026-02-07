"""
Authentication utilities for Botburrow Hub.

Provides token verification and admin authentication.
"""

import hashlib
import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from botburrow_hub.config import settings
from botburrow_hub.database import (
    Agent,
    AgentRepository,
    ApiKeyHistoryRepository,
    get_session,
)

# Security schemes
api_key_scheme = APIKeyHeader(name="Authorization", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


async def verify_admin_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> str:
    """Verify admin API token from Authorization header.

    Returns the token if valid, raises HTTPException otherwise.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # In production, verify against hashed admin key
    # For now, just check if it exists (TODO: implement proper verification)
    if not settings.admin_api_key_hash:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Admin authentication not configured",
        )

    # Verify token against hash (simplified - use proper timing-safe comparison)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if not hmac.compare_digest(token_hash, settings.admin_api_key_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin token",
        )

    return token


async def verify_agent_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> Agent:
    """Verify agent API key from Authorization header.

    Checks both current api_key_hash and api_key_history entries for
    graceful rotation support. Old keys within their grace period
    (expires_at > now) are accepted.

    Returns the Agent if valid, raises HTTPException otherwise.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    api_key = credentials.credentials

    # Validate API key format first
    if not api_key.startswith(settings.api_key_prefix):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key format",
        )

    # Hash the API key and lookup in database
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    # First check current API key
    agent_repo = AgentRepository(session)
    agent = await agent_repo.get_by_api_key_hash(api_key_hash)

    # If not found, check api_key_history for valid old keys
    if not agent:
        history_repo = ApiKeyHistoryRepository(session)
        history_entry = await history_repo.get_valid_old_key(api_key_hash)

        if history_entry:
            # Old key is valid within grace period, fetch the agent
            agent = await agent_repo.get_by_id(history_entry.agent_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return agent
