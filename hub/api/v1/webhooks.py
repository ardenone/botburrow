"""
Webhook endpoints for CI/CD integration and config cache invalidation.

Allows CI/CD workflows to:
- Receive reference-only agent registration results after OpenBao delivery
- Invalidate agent config caches when configs change in git
- Trigger git pulls to refresh agent definitions
"""

import asyncio
import hashlib
import hmac
import logging
import os
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

from botburrow_hub.auth import verify_admin_token
from botburrow_hub.cache import get_cache
from botburrow_hub.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# Webhook signature verification
def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str,
) -> bool:
    """Verify HMAC-SHA256 webhook signature."""
    if not signature:
        return False

    # Extract hash algorithm and signature
    # Format: sha256=<hex_signature>
    if signature.startswith("sha256="):
        signature = signature[7:]
    elif signature.startswith("sha1="):
        signature = signature[5:]

    # Compute expected signature
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison
    return hmac.compare_digest(signature, expected)


async def verify_ci_webhook(request: Request) -> Dict[str, str]:
    """Verify webhook signature from CI/CD system."""
    # Get webhook secret from settings
    webhook_secret = settings.ci_webhook_secret

    if not webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="CI webhook integration not configured",
        )

    # Get signature from headers
    signature = request.headers.get("X-Webhook-Signature") or request.headers.get("X-Hub-Signature-256")

    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing webhook signature",
        )

    # Read payload
    payload = await request.body()

    # Verify signature
    if not verify_webhook_signature(payload, signature, webhook_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature",
        )

    # Return CI context info
    return {
        "ci_system": request.headers.get("X-CI-System", "unknown"),
        "source": request.headers.get("X-Source-IP", "unknown"),
    }


# Models for agent registration webhook
class RegisteredAgent(BaseModel):
    """A single reference-only registration result from CI/CD."""

    name: str = Field(..., description="Agent name")
    api_key_ref: str = Field(..., description="OpenBao path for the generated key")
    config_source: str = Field(..., description="Git repository URL")
    config_path: str = Field(..., description="Path within repository")
    config_branch: str = Field(default="main", description="Git branch")
    display_name: Optional[str] = Field(None, description="Display name")
    description: Optional[str] = Field(None, description="Agent description")
    type: str = Field(default="native", description="Agent type")

    class Config:
        extra = "forbid"


class AgentRegistrationWebhook(BaseModel):
    """Webhook payload from CI/CD agent registration."""

    repository: str = Field(..., description="Git repository URL")
    branch: str = Field(..., description="Git branch")
    commit_sha: str = Field(..., description="Git commit SHA")
    run_id: Optional[str] = Field(None, description="CI run ID")
    run_url: Optional[str] = Field(None, description="CI run URL")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    agents: List[RegisteredAgent] = Field(..., description="Registered agents")

    @validator("agents")
    def at_least_one_agent(cls, v):
        if not v:
            raise ValueError("At least one agent must be registered")
        return v

    class Config:
        extra = "forbid"


class AgentRegistrationResponse(BaseModel):
    """Response to a reference-only agent registration webhook."""

    success: bool
    message: str
    timestamp: str
    repository: str
    commit_sha: str
    key_references: List[Dict[str, Any]]


@router.post(
    "/agent-registration",
    response_model=AgentRegistrationResponse,
    status_code=status.HTTP_200_OK,
)
async def agent_registration_webhook(
    webhook_data: AgentRegistrationWebhook,
    request: Request,
    _auth: Depends = Depends(verify_ci_webhook),
) -> AgentRegistrationResponse:
    """Handle agent registration webhook from CI/CD.

    This endpoint receives registration results from CI/CD workflows after the
    registration job has written each key to OpenBao. It acknowledges the
    references only; it never accepts, logs, seals, commits, or returns a
    plaintext API key.

    The webhook is secured with HMAC-SHA256 signature verification.
    """
    logger.info(
        f"Received registration webhook for {webhook_data.repository} "
        f"({len(webhook_data.agents)} agents)"
    )

    key_references = [
        {
            "agent_name": agent.name,
            "api_key_ref": agent.api_key_ref,
            "success": True,
        }
        for agent in webhook_data.agents
    ]

    return AgentRegistrationResponse(
        success=True,
        message=f"Accepted {len(key_references)} OpenBao key reference(s)",
        timestamp=datetime.now().isoformat(),
        repository=webhook_data.repository,
        commit_sha=webhook_data.commit_sha,
        key_references=key_references,
    )


@router.post(
    "/agent-registration/validation",
    response_model=Dict[str, Any],
)
async def validation_report_webhook(
    report: Dict[str, Any],
    request: Request,
    _auth: Depends = Depends(verify_ci_webhook),
) -> Dict[str, Any]:
    """Receive and store validation reports from CI/CD.

    This endpoint stores validation reports for later retrieval.
    Reports are kept in memory with a TTL.
    """
    # Store report with TTL
    key = f"validation:{report.get('repository', '')}:{report.get('commit_sha', '')}"

    # In production, store in Redis or database
    # For now, just log it
    logger.info(f"Received validation report: {key}")

    return {
        "success": True,
        "message": "Validation report received",
        "key": key,
    }


@router.get(
    "/agent-registration/validation/{repo}/{commit_sha}",
    response_model=Dict[str, Any],
)
async def get_validation_report(
    repo: str,
    commit_sha: str,
    _admin: str = Security(verify_admin_token),
) -> Dict[str, Any]:
    """Retrieve a validation report by repository and commit SHA."""
    # In production, retrieve from Redis or database
    # For now, return not found
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Validation report not found or expired",
    )


@router.post(
    "/ping",
    status_code=status.HTTP_200_OK,
)
async def webhook_ping(
    request: Request,
) -> Dict[str, str]:
    """Health check endpoint for webhook configuration.

    CI/CD systems can use this to verify webhook connectivity.
    Does not require signature verification.
    """
    return {
        "status": "ok",
        "service": "botburrow-hub-webhooks",
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================================
# Agent API Key Rotation Webhook
# ============================================================================

class AgentRotationRequest(BaseModel):
    """Request for agent API key rotation."""

    agent_name: str = Field(..., description="Name of the agent to rotate keys for")
    reason: str = Field(default="scheduled", description="Reason for rotation")
    repository: str = Field(..., description="Git repository URL for tracking")
    commit_sha: str = Field(..., description="Associated commit SHA")
    old_api_key_hash: Optional[str] = Field(None, description="Hash of old key for verification")


class AgentRotationResponse(BaseModel):
    """Reference-only response shape for the retired rotation webhook."""

    success: bool
    message: str
    agent_name: str
    api_key_ref: Optional[str] = None
    timestamp: str


@router.post(
    "/agent-rotation",
    response_model=AgentRotationResponse,
    status_code=status.HTTP_200_OK,
)
async def agent_rotation_webhook(
    rotation_request: AgentRotationRequest,
    request: Request,
    _auth: Depends = Depends(verify_ci_webhook),
) -> AgentRotationResponse:
    """Reject the retired key-carrying webhook.

    Rotation is performed by ``scripts/rotate_agent_keys.py``, which writes
    the one-time Hub response to OpenBao before emitting its reference. This
    endpoint remains as an explicit migration response so callers cannot
    accidentally reintroduce a plaintext-key webhook.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Use the OpenBao-backed rotation workflow; this webhook does not handle keys",
    )


# ============================================================================
# Config Cache Invalidation Webhook
# ============================================================================

class ConfigChangeWebhook(BaseModel):
    """Webhook payload for agent config changes.

    This webhook is triggered when agent configs change in git,
    signaling runners to invalidate their cache and pull latest configs.
    """

    repository: str = Field(..., description="Git repository URL")
    branch: str = Field(default="main", description="Git branch")
    commit_sha: str = Field(..., description="Git commit SHA that triggered change")
    commit_message: Optional[str] = Field(None, description="Commit message")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    changed_files: List[str] = Field(
        default_factory=list,
        description="List of changed file paths (for filtering specific agents)"
    )
    agent_names: Optional[List[str]] = Field(
        None,
        description="Specific agent names that changed (if known)"
    )
    trigger_git_pull: bool = Field(
        default=True,
        description="Whether runners should execute git pull after invalidation"
    )


class CacheInvalidationResponse(BaseModel):
    """Response to cache invalidation webhook."""

    success: bool
    message: str
    timestamp: str
    repository: str
    commit_sha: str
    invalidated_agents: List[str]
    cache_type: str
    git_pull_triggered: bool


def _extract_agent_names_from_paths(changed_files: List[str]) -> List[str]:
    """Extract agent names from changed file paths.

    Args:
        changed_files: List of file paths like 'agents/claude-coder-1/config.yaml'

    Returns:
        List of unique agent names that were affected
    """
    agent_names = set()

    for path in changed_files:
        # Extract agent name from patterns like:
        # - agents/agent-name/config.yaml
        # - agents/agent-name/system-prompt.md
        # - agent-name/config.yaml
        parts = path.split("/")
        for i, part in enumerate(parts):
            if part == "agents" and i + 1 < len(parts):
                agent_name = parts[i + 1]
                if agent_name and agent_name not in (".", ".."):
                    agent_names.add(agent_name)

    return sorted(agent_names)


async def _trigger_git_pull(
    config_source: str,
    branch: str,
    clone_paths: Optional[List[str]] = None,
) -> Dict[str, bool]:
    """Trigger git pull on configured repository paths.

    This is called when config changes require runners to update their
    local clone of the agent definitions repository.

    Args:
        config_source: Git repository URL
        branch: Git branch to pull
        clone_paths: Optional list of paths to update (if None, uses default)

    Returns:
        Dictionary mapping paths to success status
    """
    results = {}

    # Default clone paths based on config source
    if clone_paths is None:
        # Derive from config_source URL
        import re
        repo_name = re.sub(r'\.git$', '', config_source.split("/")[-1])
        clone_paths = [f"/configs/{repo_name}"]

    for path in clone_paths:
        try:
            # Check if path exists
            if not os.path.exists(path):
                logger.warning(f"Clone path does not exist: {path}")
                results[path] = False
                continue

            # Execute git pull
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", path, "pull", "origin", branch,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                logger.info(f"Git pull successful for {path}")
                results[path] = True
            else:
                logger.warning(f"Git pull failed for {path}: {stderr.decode()}")
                results[path] = False

        except Exception as e:
            logger.error(f"Error executing git pull for {path}: {e}")
            results[path] = False

    return results


@router.post(
    "/config-invalidation",
    response_model=CacheInvalidationResponse,
    status_code=status.HTTP_200_OK,
)
async def config_cache_invalidation(
    webhook_data: ConfigChangeWebhook,
    request: Request,
    _admin: str = Security(verify_admin_token),
) -> CacheInvalidationResponse:
    """Handle config cache invalidation webhook.

    This endpoint is called by git webhooks (Forgejo, GitHub) when agent
    configurations change. It:

    1. Invalidates the distributed cache for affected agents
    2. Publishes invalidation events to all runners via Redis pub/sub
    3. Optionally triggers git pull to update local repository clones

    This ensures all runners see the latest agent configurations without
    waiting for cache TTL to expire.

    Authentication: Requires admin API key (Bearer token)
    """
    logger.info(
        f"Received config invalidation webhook for {webhook_data.repository} "
        f"(commit: {webhook_data.commit_sha[:8]}, "
        f"files: {len(webhook_data.changed_files)})"
    )

    cache = await get_cache()

    # Determine which agents are affected
    agent_names: List[str] = []

    if webhook_data.agent_names:
        # Webhook specified exact agent names
        agent_names = webhook_data.agent_names
    elif webhook_data.changed_files:
        # Extract agent names from changed file paths
        agent_names = _extract_agent_names_from_paths(webhook_data.changed_files)

    # Invalidate cache for each affected agent
    for agent_name in agent_names:
        cache_key = f"agent:{agent_name}:{webhook_data.repository}"
        await cache.delete(cache_key)

    # Publish invalidation event to all runners
    if agent_names:
        for agent_name in agent_names:
            await cache.publish_invalidation(
                agent_name=agent_name,
                config_source=webhook_data.repository,
            )
    else:
        # No specific agents, invalidate all for this repo
        await cache.publish_invalidation(
            agent_name=None,
            config_source=webhook_data.repository,
        )

    # Trigger git pull if requested
    git_pull_triggered = False
    if webhook_data.trigger_git_pull:
        pull_results = await _trigger_git_pull(
            config_source=webhook_data.repository,
            branch=webhook_data.branch,
        )
        git_pull_triggered = any(pull_results.values())

    # Get cache stats
    cache_stats = await cache.get_stats()

    return CacheInvalidationResponse(
        success=True,
        message=f"Invalidated {len(agent_names)} agent(s)",
        timestamp=datetime.now().isoformat(),
        repository=webhook_data.repository,
        commit_sha=webhook_data.commit_sha,
        invalidated_agents=agent_names,
        cache_type=cache_stats.get("type", "memory"),
        git_pull_triggered=git_pull_triggered,
    )


@router.post(
    "/config-invalidation/all",
    response_model=CacheInvalidationResponse,
    status_code=status.HTTP_200_OK,
)
async def invalidate_all_configs(
    request: Request,
    _admin: str = Security(verify_admin_token),
) -> CacheInvalidationResponse:
    """Invalidate all cached agent configurations.

    This is a manual trigger to force all runners to reload their configs.
    Use this when you need immediate refresh across all agents and repositories.

    Authentication: Requires admin API key (Bearer token)
    """
    logger.warning("Manual invalidation of ALL agent configs requested")

    cache = await get_cache()

    # Invalidate all cache entries
    count = await cache.invalidate_all()

    # Publish global invalidation
    await cache.publish_invalidation(agent_name=None, config_source=None)

    # Get cache stats
    cache_stats = await cache.get_stats()

    return CacheInvalidationResponse(
        success=True,
        message=f"Invalidated all {count} cached entries",
        timestamp=datetime.now().isoformat(),
        repository="*",
        commit_sha="manual",
        invalidated_agents=["*"],
        cache_type=cache_stats.get("type", "memory"),
        git_pull_triggered=False,
    )


@router.get(
    "/config-invalidation/stats",
    response_model=Dict[str, Any],
)
async def get_cache_stats(
    _admin: str = Security(verify_admin_token),
) -> Dict[str, Any]:
    """Get cache statistics for monitoring.

    Returns information about cache type, size, and hit rates.

    Authentication: Requires admin API key (Bearer token)
    """
    cache = await get_cache()
    stats = await cache.get_stats()
    stats["timestamp"] = datetime.now().isoformat()
    return stats
