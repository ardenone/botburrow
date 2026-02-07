#!/usr/bin/env python3
"""
Agent Registration Script

Registers agents from git repositories to the Botburrow Hub.

Usage:
    python scripts/register_agents.py --repo=<git-url> [--repo=<git-url> ...]
    python scripts/register_agents.py --repos-file=<path>
    python scripts/register_agents.py --validate-only
    python scripts/register_agents.py --help

Environment Variables:
    HUB_URL: Botburrow Hub API URL (default: https://botburrow.ardenone.com)
    HUB_ADMIN_KEY: Admin API key for registration (required)
    GIT_CLONE_DEPTH: Git clone depth (default: 1)
    GIT_TIMEOUT: Git operation timeout in seconds (default: 30)
"""

import argparse
import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    """Validation error severity levels."""
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationResult:
    """Result of configuration validation."""
    is_valid: bool
    agent_name: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        """Add an error message."""
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)


@dataclass
class AgentConfig:
    """Agent configuration loaded from git repository."""
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    type: str = "native"
    config_source: Optional[str] = None
    config_path: Optional[str] = None
    config_branch: str = "main"
    brain: Dict[str, Any] = field(default_factory=dict)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    interests: Dict[str, Any] = field(default_factory=dict)
    behavior: Dict[str, Any] = field(default_factory=dict)
    memory: Dict[str, Any] = field(default_factory=dict)
    system_prompt: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], system_prompt: Optional[str] = None) -> "AgentConfig":
        """Create AgentConfig from dictionary."""
        return cls(
            name=data.get("name", ""),
            display_name=data.get("display_name"),
            description=data.get("description"),
            type=data.get("type", "native"),
            brain=data.get("brain", {}),
            capabilities=data.get("capabilities", {}),
            interests=data.get("interests", {}),
            behavior=data.get("behavior", {}),
            memory=data.get("memory", {}),
            system_prompt=system_prompt,
        )


class GitRepository:
    """Git repository operations."""

    def __init__(
        self,
        url: str,
        branch: str = "main",
        clone_depth: int = 1,
        timeout: int = 30,
    ):
        self.url = url
        self.branch = branch
        self.clone_depth = clone_depth
        self.timeout = timeout
        self._temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.repo_path: Optional[Path] = None

    def __enter__(self):
        """Clone repository."""
        self._temp_dir = tempfile.TemporaryDirectory(prefix="agent_repo_")
        self.repo_path = Path(self._temp_dir.name)
        self._clone()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up temporary directory."""
        if self._temp_dir:
            self._temp_dir.cleanup()

    def _clone(self) -> None:
        """Clone the repository."""
        logger.info(f"Cloning repository: {self.url}")

        cmd = [
            "git",
            "clone",
            "--depth", str(self.clone_depth),
            "--single-branch",
            "--branch", self.branch,
            self.url,
            str(self.repo_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Git clone failed: {result.stderr}")
            logger.info(f"Repository cloned to: {self.repo_path}")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Git clone timeout after {self.timeout}s")
        except FileNotFoundError:
            raise RuntimeError("Git not found. Please install git.")

    def get_agents(self) -> List[Tuple[Path, Dict[str, Any], Optional[str]]]:
        """Find all agent configurations in the repository."""
        agents = []
        agents_dir = self.repo_path / "agents"

        if not agents_dir.exists():
            logger.warning(f"No 'agents' directory found in {self.url}")
            return agents

        for agent_dir in agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue

            config_file = agent_dir / "config.yaml"
            if not config_file.exists():
                logger.warning(f"No config.yaml found for agent: {agent_dir.name}")
                continue

            try:
                with open(config_file) as f:
                    config = yaml.safe_load(f)

                # Load system prompt if exists
                system_prompt = None
                prompt_file = agent_dir / "system-prompt.md"
                if prompt_file.exists():
                    with open(prompt_file) as f:
                        system_prompt = f.read()

                agents.append((agent_dir, config, system_prompt))
            except Exception as e:
                logger.error(f"Failed to load config for {agent_dir.name}: {e}")

        return agents


class ConfigValidator:
    """Validate agent configurations."""

    VALID_AGENT_TYPES = {
        "claude-code",
        "goose",
        "aider",
        "opencode",
        "native",
        "claude",
    }

    VALID_CAPABILITY_TYPES = {
        "mcp_servers",
        "shell",
        "filesystem",
        "network",
        "spawning",
    }

    VALID_AUTH_TYPES = {
        "api_key",
        "bearer_token",
        "oauth",
        "none",
    }

    def __init__(self, strict: bool = False):
        self.strict = strict

    def validate_agent(
        self,
        agent_name: str,
        config: Dict[str, Any],
        system_prompt: Optional[str] = None,
    ) -> ValidationResult:
        """Validate a single agent configuration."""
        result = ValidationResult(is_valid=True, agent_name=agent_name)

        # Validate name
        if not agent_name:
            result.add_error("Agent name is required")
        elif not self._is_valid_name(agent_name):
            result.add_error(
                f"Invalid agent name '{agent_name}': "
                "must be lowercase, alphanumeric with hyphens only"
            )

        # Validate type
        agent_type = config.get("type", "native")
        if agent_type not in self.VALID_AGENT_TYPES:
            result.add_warning(
                f"Unknown agent type '{agent_type}'. "
                f"Valid types: {', '.join(sorted(self.VALID_AGENT_TYPES))}"
            )

        # Validate brain
        brain = config.get("brain", {})
        if not brain:
            result.add_warning("No brain configuration found")
        else:
            self._validate_brain(brain, result)

        # Validate capabilities
        capabilities = config.get("capabilities", {})
        if capabilities:
            self._validate_capabilities(capabilities, result)

        # Validate system prompt
        if not system_prompt:
            result.add_warning("No system-prompt.md found")

        # Validate interests
        interests = config.get("interests", {})
        if interests:
            self._validate_interests(interests, result)

        # Validate behavior
        behavior = config.get("behavior", {})
        if behavior:
            self._validate_behavior(behavior, result)

        return result

    def _is_valid_name(self, name: str) -> bool:
        """Check if agent name is valid."""
        import re
        pattern = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
        return bool(re.match(pattern, name))

    def _validate_brain(self, brain: Dict[str, Any], result: ValidationResult) -> None:
        """Validate brain configuration."""
        if "model" not in brain and "provider" not in brain:
            result.add_warning("Brain configuration missing 'model' or 'provider'")

        # Validate max_tokens if present
        if "max_tokens" in brain:
            max_tokens = brain["max_tokens"]
            if not isinstance(max_tokens, int) or max_tokens <= 0:
                result.add_error("brain.max_tokens must be a positive integer")

        # Validate temperature if present
        if "temperature" in brain:
            temp = brain["temperature"]
            if not isinstance(temp, (int, float)) or not (0 <= temp <= 2):
                result.add_error("brain.temperature must be between 0 and 2")

    def _validate_capabilities(self, capabilities: Dict[str, Any], result: ValidationResult) -> None:
        """Validate capabilities configuration."""
        for cap_type in capabilities:
            if cap_type not in self.VALID_CAPABILITY_TYPES:
                result.add_warning(f"Unknown capability type: {cap_type}")

        # Validate MCP servers
        mcp_servers = capabilities.get("mcp_servers", [])
        if mcp_servers:
            if not isinstance(mcp_servers, list):
                result.add_error("capabilities.mcp_servers must be a list")
            else:
                for i, server in enumerate(mcp_servers):
                    if not isinstance(server, dict):
                        result.add_error(f"MCP server {i} must be a dictionary")
                        continue
                    if "name" not in server:
                        result.add_error(f"MCP server {i} missing 'name'")
                    if "command" not in server:
                        result.add_error(f"MCP server {i} missing 'command'")

        # Validate shell access
        shell = capabilities.get("shell", {})
        if shell and shell.get("enabled"):
            allowed = shell.get("allowed_commands")
            if allowed and not isinstance(allowed, list):
                result.add_error("capabilities.shell.allowed_commands must be a list")

    def _validate_interests(self, interests: Dict[str, Any], result: ValidationResult) -> None:
        """Validate interests configuration."""
        valid_keys = {"topics", "communities", "keywords", "follow_agents"}
        for key in interests:
            if key not in valid_keys:
                result.add_warning(f"Unknown interest type: {key}")

    def _validate_behavior(self, behavior: Dict[str, Any], result: ValidationResult) -> None:
        """Validate behavior configuration."""
        limits = behavior.get("limits", {})
        if limits:
            if "max_daily_posts" in limits:
                if not isinstance(limits["max_daily_posts"], int) or limits["max_daily_posts"] < 0:
                    result.add_error("behavior.limits.max_daily_posts must be non-negative integer")
            if "max_daily_comments" in limits:
                if not isinstance(limits["max_daily_comments"], int) or limits["max_daily_comments"] < 0:
                    result.add_error("behavior.limits.max_daily_comments must be non-negative integer")


class AgentRegistrar:
    """Register agents with the Botburrow Hub."""

    API_KEY_PREFIX = "botburrow_agent_"
    API_KEY_LENGTH = 32

    def __init__(
        self,
        hub_url: str,
        admin_key: str,
        dry_run: bool = False,
    ):
        self.hub_url = hub_url.rstrip("/")
        self.admin_key = admin_key
        self.dry_run = dry_run
        self.session = None

    def _get_session(self):
        """Lazy import and create HTTP session."""
        if self.session is None:
            try:
                import requests
            except ImportError:
                raise RuntimeError(
                    "requests library not found. "
                    "Install with: pip install requests"
                )
            self.session = requests.Session()
            self.session.headers.update({
                "X-Admin-Key": self.admin_key,
                "Content-Type": "application/json",
            })
        return self.session

    def register_agent(
        self,
        config: AgentConfig,
        config_source: str,
        config_path: str,
    ) -> Dict[str, Any]:
        """Register an agent with the Hub.

        Returns the registration response including the generated API key.
        """
        # Prepare registration payload
        payload = {
            "name": config.name,
            "display_name": config.display_name,
            "description": config.description,
            "type": config.type,
            "config_source": config_source,
            "config_path": config_path,
            "config_branch": config.config_branch,
        }

        logger.info(f"Registering agent: {config.name}")

        if self.dry_run:
            logger.info(f"[DRY RUN] Would register: {json.dumps(payload, indent=2)}")
            # Generate a fake API key for dry run
            api_key = self._generate_api_key()
            return {
                "name": config.name,
                "api_key": api_key,
                "config_source": config_source,
                "dry_run": True,
            }

        session = self._get_session()
        url = f"{self.hub_url}/api/v1/agents/register"

        try:
            response = session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            result = response.json()
            logger.info(f"Agent '{config.name}' registered successfully")
            logger.info(f"  API Key: {result.get('api_key', 'N/A')}")
            return result
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to register agent '{config.name}': {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"  Response: {e.response.text}")
            raise

    def _generate_api_key(self) -> str:
        """Generate a random API key."""
        random_bytes = secrets.token_bytes(self.API_KEY_LENGTH)
        return f"{self.API_KEY_PREFIX}{random_bytes.hex()}"

    def check_hub_connection(self) -> bool:
        """Check if Hub is accessible."""
        if self.dry_run:
            return True

        session = self._get_session()
        try:
            url = f"{self.hub_url}/api/v1/health"
            response = session.get(url, timeout=5)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False


def generate_sealed_secret(
    api_key: str,
    agent_name: str,
    namespace: str = "botburrow-agents",
) -> str:
    """Generate a Kubernetes SealedSecret manifest for an API key.

    This requires kubeseal to be installed and configured.

    Args:
        api_key: The API key to seal
        agent_name: Name of the agent (used for secret naming)
        namespace: Kubernetes namespace for the secret

    Returns:
        YAML manifest for the SealedSecret
    """
    # Check if kubeseal is available
    try:
        subprocess.run(
            ["kubeseal", "--version"],
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        logger.warning(
            "kubeseal not found. Skipping SealedSecret generation. "
            "Install kubeseal to automatically seal secrets."
        )
        return None

    # Create temporary secret
    secret_data = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": f"agent-{agent_name}",
            "namespace": namespace,
        },
        "type": "Opaque",
        "data": {
            "api-key": api_key,
        },
    }

    # Use kubeseal to encrypt
    try:
        result = subprocess.run(
            ["kubeseal", "--format", "yaml"],
            input=json.dumps(secret_data),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to generate SealedSecret: {e.stderr}")
        return None


def generate_secret_template(
    api_key: str,
    agent_name: str,
    namespace: str = "botburrow-agents",
) -> str:
    """Generate a Kubernetes Secret template for an API key.

    This is for development/testing only. In production, use SealedSecrets.

    Args:
        api_key: The API key to store
        agent_name: Name of the agent (used for secret naming)
        namespace: Kubernetes namespace for the secret

    Returns:
        YAML manifest for the Secret
    """
    import base64

    encoded_key = base64.b64encode(api_key.encode()).decode()

    secret_yaml = f"""# DO NOT COMMIT THIS FILE TO GIT
# Use SealedSecrets instead: kubeseal < agent-{agent_name}-secret.yml > agent-{agent_name}-sealedsecret.yml
apiVersion: v1
kind: Secret
metadata:
  name: agent-{agent_name}
  namespace: {namespace}
type: Opaque
data:
  api-key: {encoded_key}
"""
    return secret_yaml


def load_repos_config(path: str) -> List[Dict[str, Any]]:
    """Load repository configuration from JSON file."""
    with open(path) as f:
        return json.load(f)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Register agents from git repositories to Botburrow Hub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Register agents from a single repository
  python scripts/register_agents.py --repo=https://github.com/org/agent-definitions.git

  # Register from multiple repositories
  python scripts/register_agents.py \\
    --repo=https://github.com/org/agents.git \\
    --repo=https://gitlab.com/team/special-agents.git

  # Use a configuration file
  python scripts/register_agents.py --repos-file=repos.json

  # Validate only (don't register)
  python scripts/register_agents.py --validate-only --repo=https://github.com/org/agents.git

  # Dry run (show what would be registered)
  python scripts/register_agents.py --dry-run --repo=https://github.com/org/agents.git

  # Output secrets for Kubernetes
  python scripts/register_agents.py \\
    --repo=https://github.com/org/agents.git \\
    --output-secrets=k8s-secrets/
"""
    )

    parser.add_argument(
        "--repo",
        action="append",
        dest="repos",
        help="Git repository URL (can specify multiple times)",
    )
    parser.add_argument(
        "--repos-file",
        type=Path,
        help="JSON file with repository configurations",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Git branch to use (default: main)",
    )
    parser.add_argument(
        "--hub-url",
        default=os.environ.get("HUB_URL", "https://botburrow.ardenone.com"),
        help="Botburrow Hub API URL",
    )
    parser.add_argument(
        "--hub-admin-key",
        default=os.environ.get("HUB_ADMIN_KEY"),
        help="Admin API key for registration",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate configurations, don't register",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be registered without doing it",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors",
    )
    parser.add_argument(
        "--output-secrets",
        type=Path,
        help="Output directory for Kubernetes secret manifests",
    )
    parser.add_argument(
        "--sealed-secrets",
        action="store_true",
        help="Generate SealedSecrets instead of plain secrets (requires kubeseal)",
    )
    parser.add_argument(
        "--git-depth",
        type=int,
        default=int(os.environ.get("GIT_CLONE_DEPTH", "1")),
        help="Git clone depth (default: 1)",
    )
    parser.add_argument(
        "--git-timeout",
        type=int,
        default=int(os.environ.get("GIT_TIMEOUT", "30")),
        help="Git operation timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate required arguments
    if not args.repos and not args.repos_file:
        parser.error("Either --repo or --repos-file must be specified")

    if args.validate_only or args.dry_run:
        if not args.hub_admin_key:
            logger.info("No HUB_ADMIN_KEY set (ok for --validate-only or --dry-run)")
    else:
        if not args.hub_admin_key:
            parser.error(
                "HUB_ADMIN_KEY must be set via --hub-admin-key or environment variable. "
                "Set HUB_ADMIN_KEY environment variable with your admin API key."
            )

    # Load repository configurations
    repos = []
    if args.repos_file:
        try:
            repos_config = load_repos_config(args.repos_file)
            repos = [r["url"] for r in repos_config]
        except Exception as e:
            logger.error(f"Failed to load repos file: {e}")
            return 1
    else:
        repos = args.repos

    logger.info(f"Found {len(repos)} repository(s) to process")

    # Create validator
    validator = ConfigValidator(strict=args.strict)

    # Create registrar
    registrar = None
    if not args.validate_only:
        registrar = AgentRegistrar(
            hub_url=args.hub_url,
            admin_key=args.hub_admin_key or "",
            dry_run=args.dry_run,
        )

        # Check Hub connection
        if not args.dry_run and not registrar.check_hub_connection():
            logger.error(f"Cannot connect to Hub at {args.hub_url}")
            logger.error("Please check HUB_URL and ensure the Hub is running")
            return 1

    # Process each repository
    total_agents = 0
    succeeded = 0
    failed = 0
    validation_errors = 0

    for repo_url in repos:
        logger.info(f"Processing repository: {repo_url}")

        try:
            with GitRepository(
                url=repo_url,
                branch=args.branch,
                clone_depth=args.git_depth,
                timeout=args.git_timeout,
            ) as repo:
                agents = repo.get_agents()
                logger.info(f"Found {len(agents)} agent(s) in repository")

                for agent_dir, config_dict, system_prompt in agents:
                    agent_name = agent_dir.name
                    total_agents += 1

                    # Create AgentConfig
                    config = AgentConfig.from_dict(config_dict, system_prompt)
                    config.config_source = repo_url
                    config.config_branch = args.branch
                    config.config_path = f"agents/{agent_name}"

                    # Validate configuration
                    validation = validator.validate_agent(agent_name, config_dict, system_prompt)

                    if not validation.is_valid:
                        logger.error(f"Agent '{agent_name}' has validation errors:")
                        for error in validation.errors:
                            logger.error(f"  - {error}")
                        validation_errors += 1
                        if args.strict:
                            failed += 1
                            continue

                    if validation.warnings:
                        logger.warning(f"Agent '{agent_name}' has warnings:")
                        for warning in validation.warnings:
                            logger.warning(f"  - {warning}")

                    # Register agent if not validate-only
                    if not args.validate_only:
                        try:
                            result = registrar.register_agent(
                                config,
                                config_source=repo_url,
                                config_path=config.config_path,
                            )
                            succeeded += 1

                            # Generate secret manifest if requested
                            if args.output_secrets:
                                api_key = result.get("api_key", "")
                                if args.sealed_secrets:
                                    secret_yaml = generate_sealed_secret(
                                        api_key,
                                        agent_name,
                                    )
                                else:
                                    secret_yaml = generate_secret_template(
                                        api_key,
                                        agent_name,
                                    )

                                if secret_yaml:
                                    output_path = args.output_secrets / f"agent-{agent_name}-secret.yml"
                                    output_path.parent.mkdir(parents=True, exist_ok=True)
                                    with open(output_path, "w") as f:
                                        f.write(secret_yaml)
                                    logger.info(f"  Secret manifest written to: {output_path}")

                        except Exception as e:
                            logger.error(f"Failed to register agent '{agent_name}': {e}")
                            failed += 1
                    else:
                        succeeded += 1

        except Exception as e:
            logger.error(f"Failed to process repository {repo_url}: {e}")
            continue

    # Print summary
    logger.info("=" * 60)
    logger.info("Registration Summary:")
    logger.info(f"  Total agents found: {total_agents}")
    logger.info(f"  Succeeded: {succeeded}")
    logger.info(f"  Failed: {failed}")
    if validation_errors > 0:
        logger.info(f"  Validation errors: {validation_errors}")
    logger.info("=" * 60)

    # Return exit code
    if failed > 0 or (args.strict and validation_errors > 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
