#!/usr/bin/env python3
"""
CI/CD Webhook Sender for Agent Registration

This helper script sends reference-only agent registration results from
CI/CD to the Botburrow Hub webhook endpoint. API keys are stored in OpenBao
by the registration job and are never accepted from, parsed from, or sent by
this helper.

Usage:
    python scripts/ci_webhook_sender.py \\
        --webhook-url=https://botburrow.ardenone.com/api/v1/webhooks/agent-registration \\
        --repository=$CI_REPOSITORY_URL \\
        --branch=$CI_BRANCH \\
        --commit-sha=$CI_COMMIT_SHA \\
        --run-id=$CI_RUN_ID \\
        --run-url=$CI_RUN_URL \\
        registration-results.json

Environment Variables:
    WEBHOOK_URL: Botburrow Hub webhook URL
    WEBHOOK_SECRET: Shared secret for signature verification
    CI_REPOSITORY_URL: Git repository URL
    CI_BRANCH: Git branch name
    CI_COMMIT_SHA: Git commit SHA
    CI_RUN_ID: CI run/job ID
    CI_RUN_URL: CI run/job URL
"""

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def generate_signature(payload: bytes, secret: str) -> str:
    """Generate HMAC-SHA256 webhook signature."""
    signature = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


def send_webhook(
    webhook_url: str,
    webhook_secret: str,
    repository: str,
    branch: str,
    commit_sha: str,
    agents: List[Dict[str, Any]],
    run_id: Optional[str] = None,
    run_url: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Send agent registration webhook to Hub.

    Args:
        webhook_url: Hub webhook endpoint URL
        webhook_secret: Shared secret for signature verification
        repository: Git repository URL
        branch: Git branch name
        commit_sha: Git commit SHA
        agents: List of registered agents with OpenBao references
        run_id: CI run/job ID (optional)
        run_url: CI run/job URL (optional)
        timeout: Request timeout in seconds

    Returns:
        Response from webhook endpoint
    """
    agents = reference_only_agents(agents)

    # Prepare webhook payload
    payload = {
        "repository": repository,
        "branch": branch,
        "commit_sha": commit_sha,
        "timestamp": datetime.now().isoformat(),
        "agents": agents,
    }

    if run_id:
        payload["run_id"] = run_id
    if run_url:
        payload["run_url"] = run_url

    # Serialize and sign
    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_bytes = payload_json.encode("utf-8")
    signature = generate_signature(payload_bytes, webhook_secret)

    logger.info(f"Sending webhook to: {webhook_url}")
    logger.info(f"Payload: {len(agents)} agent(s)")

    # Send request
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-CI-System": os.environ.get("CI_SYSTEM", "unknown"),
    }

    try:
        response = requests.post(
            webhook_url,
            data=payload_bytes,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()

        result = response.json()
        logger.info(f"Webhook sent successfully: {result.get('message')}")
        return result

    except requests.exceptions.RequestException as e:
        logger.error(f"Webhook failed: {e}")
        raise


def load_registration_results(results_file: Path) -> List[Dict[str, Any]]:
    """Load agent registration results from JSON file.

    The file must contain a list of agents with ``api_key_ref`` values.
    Plaintext-key fields are rejected so an old results file cannot leak a
    credential through this helper.
    """
    with open(results_file) as f:
        data = json.load(f)

    # Handle different formats
    if isinstance(data, list):
        agents = data
    elif isinstance(data, dict):
        if "agents" in data:
            agents = data["agents"]
        elif "results" in data:
            agents = data["results"]
        else:
            # Single agent
            agents = [data]
    else:
        raise ValueError(f"Unexpected format in {results_file}")

    return reference_only_agents(agents)


def reference_only_agents(agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return safe webhook fields and reject plaintext credential fields."""
    allowed_fields = {
        "name",
        "api_key_ref",
        "config_source",
        "config_path",
        "config_branch",
        "display_name",
        "description",
        "type",
    }
    forbidden_fields = {"api_key", "full_api_key", "new_api_key", "old_api_key"}
    safe_agents = []
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise ValueError(f"Agent result {index} is not an object")
        if forbidden_fields.intersection(agent):
            raise ValueError(
                f"Agent result {index} contains a plaintext API-key field; "
                "use api_key_ref from OpenBao instead"
            )
        if not agent.get("api_key_ref"):
            raise ValueError(
                f"Agent result {index} has no api_key_ref; refusing to send "
                "a credential-less or legacy result"
            )
        safe_agents.append({key: agent[key] for key in allowed_fields if key in agent})
    return safe_agents


def parse_registration_output(output: str) -> List[Dict[str, Any]]:
    """Parse registration output from register_agents.py.

    Extracts agent names and OpenBao references from script output.
    Legacy output containing a plaintext API key is rejected.
    """
    import re

    if "API Key:" in output or "api_key:" in output:
        raise ValueError(
            "Plaintext API-key output is unsupported; use registration-results.json"
        )

    agents = []
    current_name = None
    for line in output.splitlines():
        name_match = re.search(r"Agent '([^']+)' registered successfully", line)
        if name_match:
            current_name = name_match.group(1)
        ref_match = re.search(r"API key delivered to:\s*(\S+)", line)
        if ref_match:
            agents.append({
                "name": current_name or "",
                "api_key_ref": ref_match.group(1),
            })

    return reference_only_agents(agents) if agents else []


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Send agent registration results to Botburrow Hub webhook",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Send registration results from JSON file
  python scripts/ci_webhook_sender.py \\
      --webhook-url=https://botburrow.ardenone.com/api/v1/webhooks/agent-registration \\
      --repository=https://github.com/org/agents.git \\
      --branch=main \\
      --commit-sha=abc123 \\
      registration-results.json

  # Parse from script output
  python scripts/ci_webhook_sender.py \\
      --webhook-url=$WEBHOOK_URL \\
      --repository=$CI_REPO_URL \\
      --branch=$CI_BRANCH \\
      --commit-sha=$CI_COMMIT_SHA \\
      --run-id=$CI_JOB_ID \\
      --run-url=$CI_JOB_URL \\
      --parse-output registration-output.txt
"""
    )

    parser.add_argument(
        "results_file",
        type=Path,
        nargs="?",
        help="Path to registration results JSON file",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get("WEBHOOK_URL"),
        help="Botburrow Hub webhook URL",
    )
    parser.add_argument(
        "--repository",
        default=os.environ.get("CI_REPOSITORY_URL") or os.environ.get("GITHUB_REPOSITORY"),
        help="Git repository URL",
    )
    parser.add_argument(
        "--branch",
        default=os.environ.get("CI_BRANCH") or os.environ.get("GITHUB_REF_NAME"),
        help="Git branch name",
    )
    parser.add_argument(
        "--commit-sha",
        default=os.environ.get("CI_COMMIT_SHA") or os.environ.get("GITHUB_SHA"),
        help="Git commit SHA",
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("CI_RUN_ID") or os.environ.get("GITHUB_RUN_ID"),
        help="CI run/job ID",
    )
    parser.add_argument(
        "--run-url",
        default=os.environ.get("CI_RUN_URL") or os.environ.get("GITHUB_RUN_URL"),
        help="CI run/job URL",
    )
    parser.add_argument(
        "--parse-output",
        action="store_true",
        help="Parse agent info from script output instead of JSON",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be sent without sending",
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
    if not args.webhook_url:
        parser.error(
            "WEBHOOK_URL must be set via --webhook-url or environment variable"
        )
    webhook_secret = os.environ.get("WEBHOOK_SECRET")
    if not webhook_secret:
        parser.error(
            "WEBHOOK_SECRET must be set via environment variable"
        )
    if not args.repository:
        parser.error(
            "Repository must be set via --repository or CI environment variable"
        )
    if not args.branch:
        parser.error(
            "Branch must be set via --branch or CI environment variable"
        )
    if not args.commit_sha:
        parser.error(
            "Commit SHA must be set via --commit-sha or CI environment variable"
        )

    # Load agents data
    if args.parse_output:
        if not args.results_file:
            parser.error("--results-file required when using --parse-output")
        agents = parse_registration_output(args.results_file.read_text())
    else:
        if not args.results_file:
            parser.error("--results-file required (unless using --parse-output)")
        agents = load_registration_results(args.results_file)

    if not agents:
        logger.warning("No agents found in registration results")
        return 0

    logger.info(f"Loaded {len(agents)} agent(s) from {args.results_file}")

    # Add required fields if missing
    for agent in agents:
        agent.setdefault("config_source", args.repository)
        agent.setdefault("config_branch", args.branch)
        agent.setdefault("config_path", f"agents/{agent.get('name', 'unknown')}")
        agent.setdefault("type", "native")

    # Dry run
    if args.dry_run:
        logger.info("[DRY RUN] Would send the following payload:")
        payload = {
            "repository": args.repository,
            "branch": args.branch,
            "commit_sha": args.commit_sha,
            "timestamp": datetime.now().isoformat(),
            "agents": agents,
        }
        print(json.dumps(payload, indent=2))
        return 0

    # Send webhook
    try:
        result = send_webhook(
            webhook_url=args.webhook_url,
            webhook_secret=webhook_secret,
            repository=args.repository,
            branch=args.branch,
            commit_sha=args.commit_sha,
            agents=agents,
            run_id=args.run_id,
            run_url=args.run_url,
        )

        # Print result summary
        print("\n" + "=" * 60)
        print("Webhook Results:")
        print(f"  Success: {result.get('success')}")
        print(f"  Message: {result.get('message')}")
        print(f"  Repository: {result.get('repository')}")
        print(f"  Commit: {result.get('commit_sha')}")

        if result.get("commit_info"):
            info = result["commit_info"]
            print(f"  Committed to branch: {info.get('branch')}")
            print(f"  New commit: {info.get('commit_sha')}")

        print("\nOpenBao Key References:")
        for reference in result.get("key_references", []):
            status = "✓" if reference["success"] else "✗"
            print(f"  {status} {reference['agent_name']}: {reference['api_key_ref']}")

        print("=" * 60)

        return 0 if result.get("success") else 1

    except Exception as e:
        logger.error(f"Failed to send webhook: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
