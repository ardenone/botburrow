#!/usr/bin/env python3
"""
Multi-Repository Agent Config Loader

This module provides functionality for loading agent configurations from
multiple git repositories. It is used by agent runners to fetch agent
definitions from configured sources.

Usage:
    from config_loader import AgentConfigLoader

    loader = AgentConfigLoader(repos_config_path="/etc/config/repos.json")

    # Find and load a specific agent's config
    config = loader.load_agent_config(
        agent_name="claude-coder-1",
        config_source="https://github.com/org/agents.git"
    )

    # Refresh all repositories
    await loader.refresh_all_repos()
"""

import asyncio
import json
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class RepoConfig:
    """Configuration for a single git repository."""

    name: str
    url: str
    branch: str = "main"
    auth_type: str = "none"  # none, token, ssh
    auth_secret: Optional[str] = None
    clone_path: str = "/configs/default"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RepoConfig":
        """Create RepoConfig from dictionary."""
        return cls(
            name=data.get("name", "unknown"),
            url=data["url"],
            branch=data.get("branch", "main"),
            auth_type=data.get("auth_type", "none"),
            auth_secret=data.get("auth_secret"),
            clone_path=data.get("clone_path", f"/configs/{data.get('name', 'default')}"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert RepoConfig to dictionary."""
        return {
            "name": self.name,
            "url": self.url,
            "branch": self.branch,
            "auth_type": self.auth_type,
            "auth_secret": self.auth_secret,
            "clone_path": self.clone_path,
        }


@dataclass
class AgentConfig:
    """Agent configuration loaded from repository."""

    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    type: str = "native"
    brain: Dict[str, Any] = None
    capabilities: Dict[str, Any] = None
    interests: Dict[str, Any] = None
    behavior: Dict[str, Any] = None
    memory: Dict[str, Any] = None
    system_prompt: Optional[str] = None
    config_source: Optional[str] = None
    config_path: Optional[str] = None
    config_branch: str = "main"

    def __post_init__(self):
        if self.brain is None:
            self.brain = {}
        if self.capabilities is None:
            self.capabilities = {}
        if self.interests is None:
            self.interests = {}
        if self.behavior is None:
            self.behavior = {}
        if self.memory is None:
            self.memory = {}

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        system_prompt: Optional[str] = None,
        config_source: Optional[str] = None,
        config_path: Optional[str] = None,
        config_branch: str = "main",
    ) -> "AgentConfig":
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
            config_source=config_source,
            config_path=config_path,
            config_branch=config_branch,
        )


class GitRepositoryManager:
    """Manages git repository operations for multiple repos."""

    def __init__(
        self,
        repos: List[RepoConfig],
        clone_depth: int = 1,
        timeout: int = 30,
        max_workers: int = 4,
    ):
        self.repos = repos
        self.clone_depth = clone_depth
        self.timeout = timeout
        self.max_workers = max_workers

    def _build_git_url(self, repo: RepoConfig) -> str:
        """Build authenticated git URL if needed."""
        if repo.auth_type == "token" and repo.auth_secret:
            # For HTTPS with token, we need to inject the token
            # This is handled via GIT_ASKPASS or git credential helper
            return repo.url
        elif repo.auth_type == "ssh":
            # SSH URLs already contain auth via ssh key
            return repo.url
        else:
            return repo.url

    def _get_auth_env(self, repo: RepoConfig) -> Dict[str, str]:
        """Get environment variables for authentication."""
        env = os.environ.copy()

        if repo.auth_type == "token" and repo.auth_secret:
            # Try to read token from secret file or environment
            token = self._read_secret(repo.auth_secret)
            if token:
                # Use GIT_ASKPASS mechanism for token auth
                env["GIT_USERNAME"] = "token"
                env["GIT_PASSWORD"] = token
                env["GIT_TERMINAL_PROMPT"] = "0"

        return env

    def _read_secret(self, secret_ref: str) -> Optional[str]:
        """Read secret from file or Kubernetes secret mount."""
        # Try Kubernetes secret mount path first
        secret_path = Path(f"/etc/secrets/{secret_ref}/token")
        if secret_path.exists():
            return secret_path.read_text().strip()

        # Try environment variable
        env_name = secret_ref.upper().replace("-", "_")
        return os.environ.get(env_name)

    def clone_repo(self, repo: RepoConfig) -> bool:
        """Clone a single repository.

        Returns True if successful, False otherwise.
        """
        clone_path = Path(repo.clone_path)

        # Create parent directory if needed
        clone_path.parent.mkdir(parents=True, exist_ok=True)

        # Check if already exists
        if clone_path.exists():
            logger.debug(f"Repository {repo.name} already exists at {clone_path}")
            return self.pull_repo(repo)

        logger.info(f"Cloning repository: {repo.name} from {repo.url}")

        cmd = [
            "git",
            "clone",
            "--depth", str(self.clone_depth),
            "--single-branch",
            "--branch", repo.branch,
            self._build_git_url(repo),
            str(clone_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._get_auth_env(repo),
            )
            if result.returncode != 0:
                logger.error(f"Git clone failed for {repo.name}: {result.stderr}")
                return False
            logger.info(f"Repository {repo.name} cloned to {clone_path}")
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"Git clone timeout for {repo.name}")
            return False
        except Exception as e:
            logger.error(f"Git clone error for {repo.name}: {e}")
            return False

    def pull_repo(self, repo: RepoConfig) -> bool:
        """Pull latest changes for a single repository.

        Returns True if successful, False otherwise.
        """
        clone_path = Path(repo.clone_path)

        if not clone_path.exists():
            return self.clone_repo(repo)

        logger.info(f"Pulling repository: {repo.name}")

        cmd = ["git", "-C", str(clone_path), "pull", "origin", repo.branch]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._get_auth_env(repo),
            )
            if result.returncode != 0:
                logger.warning(f"Git pull failed for {repo.name}: {result.stderr}")
                return False
            logger.info(f"Repository {repo.name} updated")
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"Git pull timeout for {repo.name}")
            return False
        except Exception as e:
            logger.error(f"Git pull error for {repo.name}: {e}")
            return False

    def clone_or_pull_all(self) -> Dict[str, bool]:
        """Clone or pull all repositories in parallel.

        Returns a dictionary mapping repo names to success status.
        """
        results = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_repo = {
                executor.submit(self.clone_repo, repo): repo.name
                for repo in self.repos
            }

            for future in future_to_repo:
                repo_name = future_to_repo[future]
                try:
                    results[repo_name] = future.result()
                except Exception as e:
                    logger.error(f"Error processing {repo_name}: {e}")
                    results[repo_name] = False

        return results

    async def refresh_all_repos_async(self) -> Dict[str, bool]:
        """Async wrapper for refresh_all_repos."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.clone_or_pull_all)


class AgentConfigLoader:
    """Load agent configs from multiple git repositories."""

    def __init__(
        self,
        repos_config_path: str = "/etc/config/repos.json",
        clone_depth: int = 1,
        timeout: int = 30,
        max_workers: int = 4,
    ):
        self.repos = self._load_repos_config(repos_config_path)
        self.clone_depth = clone_depth
        self.timeout = timeout
        self.max_workers = max_workers
        self.git_manager = GitRepositoryManager(
            repos=self.repos,
            clone_depth=clone_depth,
            timeout=timeout,
            max_workers=max_workers,
        )
        self.config_cache: Dict[str, AgentConfig] = {}

    def _load_repos_config(self, path: str) -> List[RepoConfig]:
        """Load repository configuration from JSON file."""
        try:
            with open(path) as f:
                data = json.load(f)
            return [RepoConfig.from_dict(repo) for repo in data]
        except FileNotFoundError:
            logger.warning(f"Repos config file not found: {path}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Invalid repos config JSON: {e}")
            return []

    def _urls_match(self, url1: str, url2: str) -> bool:
        """Check if two git URLs refer to the same repository.

        Handles various URL formats (https, git@, .git suffix).
        """
        def normalize_url(url: str) -> str:
            # Remove protocol
            url = url.replace("https://", "").replace("http://", "")
            # Remove git@ prefix
            url = url.replace("git@", "")
            # Remove .git suffix
            url = url.removesuffix(".git")
            # Remove : after host (for SSH URLs)
            url = url.replace(":", "/", 1)
            return url.lower()

        return normalize_url(url1) == normalize_url(url2)

    def find_agent_config(
        self,
        agent_name: str,
        config_source: Optional[str] = None,
    ) -> Optional[Path]:
        """Find agent config in the correct repository.

        Args:
            agent_name: Name of the agent to find
            config_source: Git repo URL where config should be located

        Returns:
            Path to config.yaml if found, None otherwise
        """
        # First try to match by config_source
        if config_source:
            for repo in self.repos:
                if self._urls_match(repo.url, config_source):
                    config_path = (
                        Path(repo.clone_path) / "agents" / agent_name / "config.yaml"
                    )
                    if config_path.exists():
                        logger.debug(
                            f"Found config for {agent_name} in {repo.name} "
                            f"(matched config_source)"
                        )
                        return config_path

        # Fallback: search all repos
        for repo in self.repos:
            config_path = (
                Path(repo.clone_path) / "agents" / agent_name / "config.yaml"
            )
            if config_path.exists():
                logger.debug(
                    f"Found config for {agent_name} in {repo.name} (fallback search)"
                )
                return config_path

        logger.warning(f"Config for {agent_name} not found in any repo")
        return None

    def find_repo_by_config_source(self, config_source: str) -> Optional[RepoConfig]:
        """Find repository configuration by config_source URL."""
        for repo in self.repos:
            if self._urls_match(repo.url, config_source):
                return repo
        return None

    def load_agent_config(
        self,
        agent_name: str,
        config_source: Optional[str] = None,
    ) -> Optional[AgentConfig]:
        """Load agent configuration from git.

        Args:
            agent_name: Name of the agent to load
            config_source: Git repo URL where config should be located

        Returns:
            AgentConfig if found and loaded, None otherwise
        """
        cache_key = f"{agent_name}:{config_source or 'any'}"

        # Check cache first
        if cache_key in self.config_cache:
            return self.config_cache[cache_key]

        # Find config file
        config_path = self.find_agent_config(agent_name, config_source)
        if not config_path:
            return None

        try:
            # Load config.yaml
            with open(config_path) as f:
                config_data = yaml.safe_load(f)

            # Load system-prompt.md
            prompt_path = config_path.parent / "system-prompt.md"
            system_prompt = None
            if prompt_path.exists():
                with open(prompt_path) as f:
                    system_prompt = f.read()

            # Find the repo for this agent
            repo = None
            if config_source:
                repo = self.find_repo_by_config_source(config_source)
            else:
                for r in self.repos:
                    if config_path.is_relative_to(r.clone_path):
                        repo = r
                        break

            config = AgentConfig.from_dict(
                config_data,
                system_prompt=system_prompt,
                config_source=repo.url if repo else None,
                config_path=f"agents/{agent_name}",
                config_branch=repo.branch if repo else "main",
            )

            # Cache the result
            self.config_cache[cache_key] = config

            return config

        except Exception as e:
            logger.error(f"Failed to load config for {agent_name}: {e}")
            return None

    def list_agents(self) -> Dict[str, List[str]]:
        """List all agents found in all repositories.

        Returns:
            Dictionary mapping repo names to lists of agent names
        """
        agents_by_repo = {}

        for repo in self.repos:
            agents_dir = Path(repo.clone_path) / "agents"
            agents = []

            if agents_dir.exists():
                for agent_dir in agents_dir.iterdir():
                    if agent_dir.is_dir():
                        config_file = agent_dir / "config.yaml"
                        if config_file.exists():
                            agents.append(agent_dir.name)

            agents_by_repo[repo.name] = agents

        return agents_by_repo

    def refresh_all_repos(self) -> Dict[str, bool]:
        """Pull latest changes from all repos.

        Returns:
            Dictionary mapping repo names to success status
        """
        logger.info("Refreshing all agent repositories")
        results = self.git_manager.clone_or_pull_all()

        # Clear cache after refresh
        self.config_cache.clear()

        success_count = sum(1 for r in results.values() if r)
        logger.info(f"Refreshed {success_count}/{len(results)} repositories")

        return results

    async def refresh_all_repos_async(self) -> Dict[str, bool]:
        """Async version of refresh_all_repos."""
        return await self.git_manager.refresh_all_repos_async()


def load_repos_config(path: str) -> List[Dict[str, Any]]:
    """Load repository configuration from JSON file.

    Utility function for backward compatibility.
    """
    with open(path) as f:
        return json.load(f)


def get_git_info() -> tuple[str, str]:
    """Get current git commit SHA and branch.

    Returns:
        Tuple of (commit_sha, branch)
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        commit_sha = result.stdout.strip() if result.returncode == 0 else "unknown"

        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = result.stdout.strip() if result.returncode == 0 else "unknown"

        return commit_sha, branch
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown", "unknown"


# CLI for testing
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Agent Config Loader CLI")
    parser.add_argument(
        "--repos-file",
        default="/etc/config/repos.json",
        help="Path to repos.json configuration",
    )
    parser.add_argument(
        "--agent",
        help="Name of agent to load",
    )
    parser.add_argument(
        "--config-source",
        help="Git repo URL where agent config is located",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all agents in all repos",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh all repositories",
    )

    args = parser.parse_args()

    loader = AgentConfigLoader(repos_config_path=args.repos_file)

    if args.refresh:
        results = loader.refresh_all_repos()
        for repo, success in results.items():
            status = "OK" if success else "FAILED"
            print(f"{repo}: {status}")

    if args.list:
        agents = loader.list_agents()
        for repo, agent_list in agents.items():
            print(f"{repo}: {', '.join(agent_list) if agent_list else '(no agents)'}")

    if args.agent:
        config = loader.load_agent_config(args.agent, args.config_source)
        if config:
            print(f"Agent: {config.name}")
            print(f"Type: {config.type}")
            print(f"Display Name: {config.display_name or 'N/A'}")
            print(f"Config Source: {config.config_source or 'N/A'}")
            print(f"System Prompt: {len(config.system_prompt or 0)} chars")
        else:
            print(f"Agent '{args.agent}' not found")
