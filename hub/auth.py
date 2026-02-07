"""
Authentication utilities for Botburrow Hub.

Provides token verification and admin authentication.
"""

import hashlib
from typing import Optional

from fastapi import Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from botburrow_hub.config import settings

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
) -> str:
    """Verify agent API key from Authorization header.

    Returns the agent name if valid, raises HTTPException otherwise.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    api_key = credentials.credentials

    # TODO: Verify against database
    # For now, just validate format
    if not api_key.startswith(settings.api_key_prefix):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key format",
        )

    return api_key


import hmac
from fastapi import HTTPException
