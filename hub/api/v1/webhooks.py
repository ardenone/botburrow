"""
Webhook endpoints for CI/CD integration and config cache invalidation.

Allows CI/CD workflows to:
- Deliver agent registration results (including API keys) for SealedSecret creation
- Invalidate agent config caches when configs change in git
- Trigger git pulls to refresh agent definitions
"""

import asyncio
import hashlib
import hmac
import logging
import os
import subprocess
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import yaml
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
    """A single registered agent from CI/CD."""

    name: str = Field(..., description="Agent name")
    api_key: str = Field(..., description="Generated API key")
    config_source: str = Field(..., description="Git repository URL")
    config_path: str = Field(..., description="Path within repository")
    config_branch: str = Field(default="main", description="Git branch")
    display_name: Optional[str] = Field(None, description="Display name")
    description: Optional[str] = Field(None, description="Agent description")
    type: str = Field(default="native", description="Agent type")


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


class SealedSecretResult(BaseModel):
    """Result of SealedSecret generation."""

    agent_name: str
    secret_name: str
    namespace: str
    success: bool
    error: Optional[str] = None
    manifest: Optional[str] = None


class AgentRegistrationResponse(BaseModel):
    """Response to agent registration webhook."""

    success: bool
    message: str
    timestamp: str
    repository: str
    commit_sha: str
    secrets_created: List[SealedSecretResult]
    commit_info: Optional[Dict[str, str]] = None


async def generate_sealed_secret(
    api_key: str,
    agent_name: str,
    namespace: str = "botburrow-agents",
) -> SealedSecretResult:
    """Generate a SealedSecret for an agent API key.

    This requires kubeseal to be available in the environment.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    secret_name = f"agent-{agent_name}"

    # Create temporary secret manifest
    secret_manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": secret_name,
            "namespace": namespace,
            "annotations": {
                "botburrow.ardenone.com/agent-name": agent_name,
                "botburrow.ardenone.com/created-at": datetime.now().isoformat(),
            },
        },
        "type": "Opaque",
        "data": {
            "api-key": api_key,
        },
    }

    try:
        # Check if kubeseal is available
        try:
            subprocess.run(
                ["kubeseal", "--version"],
                capture_output=True,
                check=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            logger.warning("kubeseal not found, cannot generate SealedSecret")
            return SealedSecretResult(
                agent_name=agent_name,
                secret_name=secret_name,
                namespace=namespace,
                success=False,
                error="kubeseal not available",
            )

        # Write secret to temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(secret_manifest, f)
            temp_path = f.name

        try:
            # Seal the secret
            result = subprocess.run(
                ["kubeseal", "--format", "yaml", "--cert", "/etc/kubeseal/cert.pem"],
                input=Path(temp_path).read_text(),
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )

            # Write SealedSecret to manifests directory
            manifests_dir = Path(settings.sealed_secrets_output_dir)
            manifests_dir.mkdir(parents=True, exist_ok=True)

            sealed_secret_path = manifests_dir / f"{secret_name}-sealedsecret.yml"
            sealed_secret_path.write_text(result.stdout)

            # Commit to git if configured
            if settings.auto_commit_secrets:
                _commit_sealed_secret(sealed_secret_path, agent_name, commit_sha="")

            return SealedSecretResult(
                agent_name=agent_name,
                secret_name=secret_name,
                namespace=namespace,
                success=True,
                manifest=result.stdout,
            )

        finally:
            # Clean up temp file
            Path(temp_path).unlink(missing_ok=True)

    except subprocess.TimeoutExpired:
        return SealedSecretResult(
            agent_name=agent_name,
            secret_name=secret_name,
            namespace=namespace,
            success=False,
            error="kubeseal timeout",
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"kubeseal failed: {e.stderr}")
        return SealedSecretResult(
            agent_name=agent_name,
            secret_name=secret_name,
            namespace=namespace,
            success=False,
            error=f"kubeseal failed: {e.stderr[:200]}",
        )
    except Exception as e:
        logger.exception(f"Failed to generate SealedSecret for {agent_name}")
        return SealedSecretResult(
            agent_name=agent_name,
            secret_name=secret_name,
            namespace=namespace,
            success=False,
            error=str(e),
        )


def _commit_sealed_secret(
    secret_path: Path,
    agent_name: str,
    commit_sha: str,
) -> Optional[Dict[str, str]]:
    """Commit a SealedSecret to git."""
    import subprocess

    try:
        # Get git repo info
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = result.stdout.strip() if result.returncode == 0 else "unknown"

        # Add file
        subprocess.run(
            ["git", "add", str(secret_path)],
            capture_output=True,
            timeout=10,
        )

        # Commit
        commit_message = f"chore: add SealedSecret for agent {agent_name}\n\nAuto-generated from CI/CD registration\n"
        if commit_sha:
            commit_message += f"CI commit: {commit_sha}\n"

        result = subprocess.run(
            ["git", "commit", "-m", commit_message],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            # Get new commit SHA
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            new_sha = result.stdout.strip() if result.returncode == 0 else ""

            return {
                "branch": branch,
                "commit_sha": new_sha,
                "file": str(secret_path),
            }

    except Exception as e:
        logger.error(f"Failed to commit SealedSecret: {e}")

    return None


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

    This endpoint receives registration results from CI/CD workflows and:
    1. Generates SealedSecrets for each agent's API key
    2. Commits the secrets to the repository (if configured)
    3. Returns commit information for CI/CD to reference

    The webhook is secured with HMAC-SHA256 signature verification.
    """
    logger.info(
        f"Received registration webhook for {webhook_data.repository} "
        f"({len(webhook_data.agents)} agents)"
    )

    secrets_created = []

    for agent in webhook_data.agents:
        logger.info(f"Generating SealedSecret for agent: {agent.name}")

        result = await generate_sealed_secret(
            api_key=agent.api_key,
            agent_name=agent.name,
            namespace="botburrow-agents",
        )

        secrets_created.append(result)

        if result.success:
            logger.info(f"Created SealedSecret: {result.secret_name}")
        else:
            logger.error(f"Failed to create SealedSecret for {agent.name}: {result.error}")

    # Get commit info if any were committed
    commit_info = None
    successful_commits = [s.commit_info for s in secrets_created if s.commit_info]
    if successful_commits:
        commit_info = {
            "branch": successful_commits[0].get("branch", ""),
            "commit_sha": successful_commits[0].get("commit_sha", ""),
        }

    # Determine overall success
    all_success = all(s.success for s in secrets_created)
    failed_count = sum(1 for s in secrets_created if not s.success)

    if all_success:
        message = f"Successfully created {len(secrets_created)} SealedSecret(s)"
    elif failed_count == len(secrets_created):
        message = "Failed to create any SealedSecrets"
    else:
        message = f"Created {len(secrets_created) - failed_count}/{len(secrets_created)} SealedSecret(s)"

    return AgentRegistrationResponse(
        success=all_success,
        message=message,
        timestamp=datetime.now().isoformat(),
        repository=webhook_data.repository,
        commit_sha=webhook_data.commit_sha,
        secrets_created=secrets_created,
        commit_info=commit_info,
    )


@router.post(
    "/agent-registration/validation",
    response_model=Dict[str, any],
)
async def validation_report_webhook(
    report: Dict[str, any],
    request: Request,
    _auth: Depends = Depends(verify_ci_webhook),
) -> Dict[str, any]:
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
    response_model=Dict[str, any],
)
async def get_validation_report(
    repo: str,
    commit_sha: str,
    _admin: str = Security(verify_admin_token),
) -> Dict[str, any]:
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
