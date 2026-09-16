#!/usr/bin/env python3
"""
Agent API Key Rotation Script

Rotates API keys for agents with zero downtime using grace period support.
This script integrates with the Hub API and stores each newly generated key
in OpenBao. Reports contain only the OpenBao retrieval path.

Usage:
    python scripts/rotate_agent_keys.py --all
    python scripts/rotate_agent_keys.py --agent-name claude-coder-1
    python scripts/rotate_agent_keys.py --grace-period 48

Environment Variables:
    HUB_URL: Botburrow Hub API URL (default: https://botburrow.ardenone.com)
    HUB_ADMIN_KEY: Admin API key for rotation (required)
    OPENBAO_TOKEN_FILE: Provisioning-identity token file (preferred)
    OPENBAO_TOKEN: Provisioning-identity token (fallback)
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from openbao_store import (
    DEFAULT_KV_MOUNT,
    DEFAULT_PATH_PREFIX,
    OpenBaoClient,
    OpenBaoDeliveryError,
)

try:
    import requests
except ImportError:
    print("Error: requests library not found. Install with: pip install requests")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class RotationResult:
    """Result of API key rotation for a single agent."""
    agent_name: str
    agent_id: str
    success: bool
    api_key_ref: Optional[str] = None
    old_key_expires_at: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RotationReport:
    """Complete rotation report for CI/CD."""
    timestamp: str
    trigger: str
    grace_period_hours: int
    total_agents: int
    succeeded: int
    failed: int
    results: List[Dict[str, Any]]
    summary: str

    def to_json(self, indent: int = 2) -> str:
        """Convert report to JSON string."""
        return json.dumps(asdict(self), indent=indent)

    def to_markdown(self) -> str:
        """Convert report to Markdown for PR comments."""
        lines = [
            "## API Key Rotation Report",
            "",
            f"**Timestamp:** {self.timestamp}",
            f"**Trigger:** {self.trigger}",
            f"**Grace Period:** {self.grace_period_hours} hours",
            "",
            "### Summary",
            "",
            f"| Metric | Count |",
            f"|--------|-------|",
            f"| Total Agents | {self.total_agents} |",
            f"| Succeeded | {self.succeeded} |",
            f"| Failed | {self.failed} |",
            "",
        ]

        if self.failed > 0:
            lines.extend([
                "### ❌ Failed Rotations",
                "",
            ])
            for result in self.results:
                if not result.get("success"):
                    lines.append(f"#### `{result['agent_name']}`")
                    lines.append(f"- Error: {result.get('error', 'Unknown')}")
                    lines.append("")

        lines.extend([
            "---",
            self.summary,
        ])

        return "\n".join(lines)


class APIKeyRotator:
    """Handles API key rotation for agents."""

    def __init__(
        self,
        hub_url: str,
        admin_key: str,
        grace_period_hours: int = 24,
        key_store: Optional[OpenBaoClient] = None,
    ):
        self.hub_url = hub_url.rstrip("/")
        self.admin_key = admin_key
        self.grace_period_hours = grace_period_hours
        self.key_store = key_store
        self.session = None

    def _get_session(self):
        """Lazy import and create HTTP session."""
        if self.session is None:
            self.session = requests.Session()
            self.session.headers.update({
                "X-Admin-Key": self.admin_key,
                "Content-Type": "application/json",
            })
        return self.session

    def list_agents(self) -> List[Dict[str, Any]]:
        """List all agents from the Hub."""
        session = self._get_session()
        url = f"{self.hub_url}/api/v1/agents"
        try:
            response = session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get("agents", [])
        except Exception as e:
            logger.error(f"Failed to list agents: {e}")
            return []

    def get_agent_by_name(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Get a specific agent by name."""
        session = self._get_session()
        url = f"{self.hub_url}/api/v1/agents/{agent_name}"
        try:
            response = session.get(url, timeout=10)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get agent {agent_name}: {e}")
            return None

    def rotate_agent_key(self, agent_name: str) -> RotationResult:
        """Rotate API key for a specific agent.

        The Hub returns the new key once. It is written to OpenBao and only
        the resulting retrieval path is retained in the rotation result.

        Note: The agent must call this endpoint itself using its own API key.
        For admin-initiated rotation, we use the agent's profile endpoint.
        """
        # Get agent info first
        agent_info = self.get_agent_by_name(agent_name)
        if not agent_info:
            return RotationResult(
                agent_name=agent_name,
                agent_id="",
                success=False,
                error=f"Agent '{agent_name}' not found"
            )

        agent_id = agent_info.get("id", "")

        # For admin-initiated rotation, we need to generate a new key
        # This requires the agent to call regenerate-key or admin to rotate
        # For now, we'll use the regenerate-key endpoint with admin auth

        session = self._get_session()

        # First, get the current agent's API key hash
        # Then we need to call regenerate-key on behalf of the agent
        # This is a limitation - the regenerate-key endpoint requires agent auth

        # Alternative: Use admin endpoint to rotate key (if implemented)
        # For now, we'll return an error indicating manual rotation needed

        logger.warning(f"Admin-initiated rotation for '{agent_name}' requires agent to call regenerate-key endpoint")

        # Calculate grace period expiration
        grace_expires_at = datetime.now() + timedelta(hours=self.grace_period_hours)

        # For this implementation, we'll generate a new key via a direct API call
        # This assumes there's an admin endpoint for key rotation
        url = f"{self.hub_url}/api/v1/admin/agents/{agent_id}/rotate"
        try:
            response = session.post(
                url,
                json={
                    "grace_period_hours": self.grace_period_hours,
                },
                timeout=10
            )
            if response.status_code == 404:
                # Admin endpoint not implemented, use agent endpoint
                logger.info(f"Admin rotate endpoint not found, agent must rotate its own key")
                return RotationResult(
                    agent_name=agent_name,
                    agent_id=agent_id,
                    success=False,
                    error="Agent must rotate its own key via /api/v1/agents/me/regenerate-key"
                )

            response.raise_for_status()
            data = response.json()

            new_api_key = data.get("api_key")
            if not new_api_key:
                return RotationResult(
                    agent_name=agent_name,
                    agent_id=agent_id,
                    success=False,
                    error="Hub did not return a new API key; refusing to continue",
                )
            if self.key_store is None:
                return RotationResult(
                    agent_name=agent_name,
                    agent_id=agent_id,
                    success=False,
                    error="OpenBao key store is not configured; refusing to drop the new key",
                )

            try:
                key_ref = self.key_store.deliver(agent_name, new_api_key)
            except OpenBaoDeliveryError as e:
                return RotationResult(
                    agent_name=agent_name,
                    agent_id=agent_id,
                    success=False,
                    error=str(e),
                )

            return RotationResult(
                agent_name=agent_name,
                agent_id=agent_id,
                success=True,
                api_key_ref=key_ref.full_path,
                old_key_expires_at=data.get("old_key_expires_at", grace_expires_at.isoformat()),
            )

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to rotate key for '{agent_name}': {e}")
            return RotationResult(
                agent_name=agent_name,
                agent_id=agent_id,
                success=False,
                error="Hub request failed; see the request status and retry",
            )


def generate_rotation_report(
    results: List[RotationResult],
    trigger: str,
    grace_period_hours: int,
) -> RotationReport:
    """Generate a rotation report from results."""
    total = len(results)
    succeeded = sum(1 for r in results if r.success)
    failed = total - succeeded

    # Generate summary
    if failed == 0:
        summary = f"✅ Successfully rotated {total} agent API key(s) with {grace_period_hours}h grace period."
    elif succeeded == 0:
        summary = f"❌ Failed to rotate all {total} agent API key(s)."
    else:
        summary = f"⚠️ Rotated {succeeded}/{total} agent API key(s), {failed} failed."

    return RotationReport(
        timestamp=datetime.now().isoformat(),
        trigger=trigger,
        grace_period_hours=grace_period_hours,
        total_agents=total,
        succeeded=succeeded,
        failed=failed,
        results=[asdict(r) for r in results],
        summary=summary,
    )


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Rotate agent API keys with zero downtime",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Rotate all agent API keys
  python scripts/rotate_agent_keys.py --all

  # Rotate specific agent
  python scripts/rotate_agent_keys.py --agent-name claude-coder-1

  # Rotate with 48 hour grace period
  python scripts/rotate_agent_keys.py --all --grace-period 48

  # Store newly generated keys in OpenBao and emit references only
  OPENBAO_TOKEN_FILE=/run/secrets/openbao-token \
    python scripts/rotate_agent_keys.py --all
"""
    )

    parser.add_argument(
        "--agent-name",
        help="Specific agent name to rotate (use --all for all agents)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Rotate all agents",
    )
    parser.add_argument(
        "--hub-url",
        default=os.environ.get("HUB_URL", "https://botburrow.ardenone.com"),
        help="Botburrow Hub API URL",
    )
    parser.add_argument(
        "--grace-period",
        type=int,
        default=24,
        help="Grace period in hours for old key validity (default: 24)",
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        default=Path("rotation-report.json"),
        help="Output path for rotation report",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        help="Output path for markdown report",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate required arguments
    admin_key = os.environ.get("HUB_ADMIN_KEY")
    if not admin_key:
        parser.error(
            "HUB_ADMIN_KEY must be set via environment variable. "
            "Set HUB_ADMIN_KEY environment variable with your admin API key."
        )

    if not args.all and not args.agent_name:
        parser.error("Either --all or --agent-name must be specified")

    if args.all and args.agent_name:
        parser.error("Cannot specify both --all and --agent-name")

    try:
        key_store = OpenBaoClient(
            mount=os.environ.get("OPENBAO_KV_MOUNT", DEFAULT_KV_MOUNT),
            prefix=os.environ.get("OPENBAO_SECRET_PREFIX", DEFAULT_PATH_PREFIX),
        )
    except OpenBaoDeliveryError as e:
        logger.error("OpenBao provisioning setup failed: %s", e)
        return 1

    # Create rotator
    rotator = APIKeyRotator(
        hub_url=args.hub_url,
        admin_key=admin_key,
        grace_period_hours=args.grace_period,
        key_store=key_store,
    )

    # Check Hub connection
    try:
        session = rotator._get_session()
        url = f"{args.hub_url}/api/v1/health"
        response = session.get(url, timeout=5)
        if response.status_code != 200:
            logger.error(f"Hub health check failed: {response.status_code}")
            return 1
    except Exception as e:
        logger.error(f"Cannot connect to Hub at {args.hub_url}: {e}")
        return 1

    # Determine agents to rotate
    agent_names = []
    if args.all:
        agents = rotator.list_agents()
        agent_names = [a["name"] for a in agents]
        logger.info(f"Found {len(agent_names)} agents to rotate")
    else:
        agent_names = [args.agent_name]

    # Rotate keys
    results = []
    for agent_name in agent_names:
        logger.info(f"Rotating API key for: {agent_name}")
        result = rotator.rotate_agent_key(agent_name)
        results.append(result)

        if result.success:
            logger.info(
                f"  ✓ New API key stored at: {result.api_key_ref} "
                f"(old key expires: {result.old_key_expires_at})"
            )
        else:
            logger.error(f"  ✗ Failed: {result.error}")

    # Generate report
    trigger = "manual" if args.agent_name else "scheduled"
    report = generate_rotation_report(
        results,
        trigger=trigger,
        grace_period_hours=args.grace_period,
    )

    # Write JSON report
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_report, "w") as f:
        f.write(report.to_json())
    logger.info(f"Rotation report written to: {args.output_report}")

    # Write Markdown report if requested
    if args.output_markdown:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_markdown, "w") as f:
            f.write(report.to_markdown())
        logger.info(f"Markdown report written to: {args.output_markdown}")

    # Print summary
    logger.info("=" * 60)
    logger.info("Rotation Summary:")
    logger.info(f"  Total: {report.total_agents}")
    logger.info(f"  Succeeded: {report.succeeded}")
    logger.info(f"  Failed: {report.failed}")
    logger.info(f"  {report.summary}")
    logger.info("=" * 60)

    # Print markdown to stdout for CI consumption
    if args.verbose:
        print("\n" + report.to_markdown())

    # Return exit code
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
