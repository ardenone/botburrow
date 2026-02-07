#!/usr/bin/env python3
"""
CI/CD Webhook Sender for Agent Registration

This helper script sends agent registration results from CI/CD to the
Botburrow Hub webhook endpoint for secure API key storage.

Usage:
    python scripts/ci_webhook_sender.py \\
        --webhook-url=https://botburrow.ardenone.com/api/v1/webhooks/agent-registration \\
        --webhook-secret=$WEBHOOK_SECRET \\
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
        agents: List of registered agents with API keys
        run_id: CI run/job ID (optional)
        run_url: CI run/job URL (optional)
        timeout: Request timeout in seconds

    Returns:
        Response from webhook endpoint
    """
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
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        raise


def load_registration_results(results_file: Path) -> List[Dict[str, Any]]:
    """Load agent registration results from JSON file.

    The file should contain a list of agents with their API keys.
    Format can vary - this tries multiple common formats.
    """
    with open(results_file) as f:
        data = json.load(f)

    # Handle different formats
    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        if "agents" in data:
            return data["agents"]
        elif "results" in data:
            return data["results"]
        else:
            # Single agent
            return [data]
    else:
        raise ValueError(f"Unexpected format in {results_file}")


def parse_registration_output(output: str) -> List[Dict[str, Any]]:
    """Parse registration output from register_agents.py.

    Extracts agent names and API keys from script output.
    This is a fallback when JSON results aren't available.
    """
    agents = []
    lines = output.splitlines()

    # Look for API key patterns in output
    for i, line in enumerate(lines):
        if "API Key:" in line or "api_key:" in line:
            # Extract API key
            key = line.split(":")[-1].strip()

            # Try to get agent name from previous line (which has 'name' in quotes)
            if i > 0:
                prev_line = lines[i - 1]
                if "'" in prev_line:
                    parts = prev_line.split("'")
                    if len(parts) >= 2:
                        name = parts[1]
                        if key.startswith("botburrow_agent_"):
                            agents.append({
                                "name": name,
                                "api_key": key,
                            })
                            continue

            # Fallback: try to extract from current line if name is present
            # Format: "API Key: botburrow_agent_xxx (for agent-name)" or similar
            if key.startswith("botburrow_agent_"):
                # If we can't find a name, still add the agent with empty name
                # The caller can fill it in from context
                agents.append({
                    "name": "",
                    "api_key": key,
                })

    return agents


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
      --webhook-secret=$WEBHOOK_SECRET \\
      --repository=https://github.com/org/agents.git \\
      --branch=main \\
      --commit-sha=abc123 \\
      registration-results.json

  # Parse from script output
  python scripts/ci_webhook_sender.py \\
      --webhook-url=$WEBHOOK_URL \\
      --webhook-secret=$WEBHOOK_SECRET \\
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
        "--webhook-secret",
        default=os.environ.get("WEBHOOK_SECRET"),
        help="Shared secret for webhook signature verification",
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
    if not args.webhook_secret:
        parser.error(
            "WEBHOOK_SECRET must be set via --webhook-secret or environment variable"
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
            webhook_secret=args.webhook_secret,
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

        print("\nSecrets Created:")
        for secret in result.get("secrets_created", []):
            status = "✓" if secret["success"] else "✗"
            print(f"  {status} {secret['agent_name']}: {secret['secret_name']}")
            if not secret["success"]:
                print(f"      Error: {secret.get('error')}")

        print("=" * 60)

        return 0 if result.get("success") else 1

    except Exception as e:
        logger.error(f"Failed to send webhook: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
