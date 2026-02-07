"""
Tests for multi-repo agent config loader.

Tests cover:
- Repository configuration loading
- URL matching and normalization
- Agent config discovery
- Parallel git clone/pull operations
- Cache management
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest
import yaml

# Import the modules to test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config_loader import (
    RepoConfig,
    AgentConfig,
    GitRepositoryManager,
    AgentConfigLoader,
    load_repos_config,
)


class TestRepoConfig:
    """Test RepoConfig dataclass."""

    def test_from_dict_minimal(self):
        """Test creating RepoConfig from minimal dictionary."""
        data = {"name": "test-repo", "url": "https://github.com/test/repo.git"}
        config = RepoConfig.from_dict(data)
        assert config.name == "test-repo"
        assert config.url == "https://github.com/test/repo.git"
        assert config.branch == "main"  # default
        assert config.auth_type == "none"  # default

    def test_from_dict_full(self):
        """Test creating RepoConfig from full dictionary."""
        data = {
            "name": "test-repo",
            "url": "https://github.com/test/repo.git",
            "branch": "develop",
            "auth_type": "token",
            "auth_secret": "github-token",
            "clone_path": "/configs/test",
        }
        config = RepoConfig.from_dict(data)
        assert config.name == "test-repo"
        assert config.branch == "develop"
        assert config.auth_type == "token"
        assert config.auth_secret == "github-token"
        assert config.clone_path == "/configs/test"

    def test_to_dict(self):
        """Test converting RepoConfig to dictionary."""
        config = RepoConfig(
            name="test-repo",
            url="https://github.com/test/repo.git",
            branch="main",
            auth_type="none",
            clone_path="/configs/test",
        )
        data = config.to_dict()
        assert data["name"] == "test-repo"
        assert data["url"] == "https://github.com/test/repo.git"
        assert data["branch"] == "main"
        assert data["clone_path"] == "/configs/test"


class TestAgentConfig:
    """Test AgentConfig dataclass."""

    def test_from_dict_minimal(self):
        """Test creating AgentConfig from minimal dictionary."""
        data = {"name": "test-agent"}
        config = AgentConfig.from_dict(data)
        assert config.name == "test-agent"
        assert config.type == "native"  # default
        assert config.brain == {}
        assert config.capabilities == {}

    def test_from_dict_full(self):
        """Test creating AgentConfig from full dictionary."""
        data = {
            "name": "test-agent",
            "display_name": "Test Agent",
            "description": "A test agent",
            "type": "claude-code",
            "brain": {"model": "claude-sonnet-4"},
            "capabilities": {"shell": {"enabled": True}},
        }
        config = AgentConfig.from_dict(
            data,
            system_prompt="You are helpful.",
            config_source="https://github.com/test/repo.git",
            config_path="agents/test-agent",
        )
        assert config.name == "test-agent"
        assert config.display_name == "Test Agent"
        assert config.type == "claude-code"
        assert config.brain["model"] == "claude-sonnet-4"
        assert config.system_prompt == "You are helpful."
        assert config.config_source == "https://github.com/test/repo.git"
        assert config.config_path == "agents/test-agent"


class TestGitRepositoryManager:
    """Test GitRepositoryManager class."""

    def test_init(self):
        """Test GitRepositoryManager initialization."""
        repos = [
            RepoConfig(name="repo1", url="https://github.com/test/repo1.git"),
            RepoConfig(name="repo2", url="https://github.com/test/repo2.git"),
        ]
        manager = GitRepositoryManager(repos=repos, clone_depth=1, timeout=30)
        assert manager.repos == repos
        assert manager.clone_depth == 1
        assert manager.timeout == 30

    def test_build_git_url_https(self):
        """Test building git URL for HTTPS."""
        repo = RepoConfig(name="test", url="https://github.com/test/repo.git")
        manager = GitRepositoryManager(repos=[repo])
        assert manager._build_git_url(repo) == "https://github.com/test/repo.git"

    def test_build_git_url_ssh(self):
        """Test building git URL for SSH."""
        repo = RepoConfig(name="test", url="git@github.com:test/repo.git")
        manager = GitRepositoryManager(repos=[repo])
        assert manager._build_git_url(repo) == "git@github.com:test/repo.git"

    @patch('subprocess.run')
    def test_clone_repo_success(self, mock_run):
        """Test successful repository clone."""
        mock_run.return_value = Mock(returncode=0, stderr="")

        repo = RepoConfig(
            name="test-repo",
            url="https://github.com/test/repo.git",
            clone_path="/tmp/test_repo",
        )
        manager = GitRepositoryManager(repos=[repo])

        with tempfile.TemporaryDirectory() as tmpdir:
            repo.clone_path = Path(tmpdir) / "test_repo"
            result = manager.clone_repo(repo)

            assert result is True
            assert mock_run.called

    @patch('subprocess.run')
    def test_clone_repo_failure(self, mock_run):
        """Test failed repository clone."""
        mock_run.return_value = Mock(
            returncode=1,
            stderr="fatal: repository not found"
        )

        repo = RepoConfig(
            name="test-repo",
            url="https://github.com/nonexistent/repo.git",
            clone_path="/tmp/test_repo",
        )
        manager = GitRepositoryManager(repos=[repo])

        with tempfile.TemporaryDirectory() as tmpdir:
            repo.clone_path = Path(tmpdir) / "test_repo"
            result = manager.clone_repo(repo)

            assert result is False

    @patch('subprocess.run')
    def test_pull_repo_existing(self, mock_run):
        """Test pulling an existing repository."""
        mock_run.return_value = Mock(returncode=0, stderr="")

        repo = RepoConfig(
            name="test-repo",
            url="https://github.com/test/repo.git",
        )
        manager = GitRepositoryManager(repos=[repo])

        with tempfile.TemporaryDirectory() as tmpdir:
            repo.clone_path = Path(tmpdir)
            # Create .git directory to simulate existing repo
            (repo.clone_path / ".git").mkdir()

            result = manager.pull_repo(repo)

            assert result is True
            # Verify pull command was called
            cmd = mock_run.call_args[0][0]
            assert "pull" in cmd

    @patch('subprocess.run')
    def test_clone_or_pull_all_parallel(self, mock_run):
        """Test parallel clone/pull of all repositories."""
        mock_run.return_value = Mock(returncode=0, stderr="")

        repos = [
            RepoConfig(name=f"repo{i}", url=f"https://github.com/test/repo{i}.git")
            for i in range(3)
        ]
        manager = GitRepositoryManager(repos=repos, max_workers=2)

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, repo in enumerate(repos):
                repo.clone_path = Path(tmpdir) / f"repo{i}"
                # Create directories to simulate existing repos
                repo.clone_path.mkdir()

            results = manager.clone_or_pull_all()

            assert len(results) == 3
            assert all(results.values())


class TestAgentConfigLoader:
    """Test AgentConfigLoader class."""

    def test_load_repos_config(self, tmp_path):
        """Test loading repository configuration from JSON file."""
        config_file = tmp_path / "repos.json"
        config_data = [
            {
                "name": "repo1",
                "url": "https://github.com/test/repo1.git",
                "branch": "main",
                "auth_type": "none",
                "clone_path": "/configs/repo1",
            },
            {
                "name": "repo2",
                "url": "https://github.com/test/repo2.git",
                "branch": "develop",
                "auth_type": "token",
                "auth_secret": "token-secret",
                "clone_path": "/configs/repo2",
            },
        ]
        config_file.write_text(json.dumps(config_data))

        loader = AgentConfigLoader(repos_config_path=str(config_file))

        assert len(loader.repos) == 2
        assert loader.repos[0].name == "repo1"
        assert loader.repos[1].branch == "develop"

    def test_urls_match(self):
        """Test URL matching and normalization."""
        loader = AgentConfigLoader(repos_config_path="/nonexistent.json")

        # Same URLs should match
        assert loader._urls_match(
            "https://github.com/test/repo.git",
            "https://github.com/test/repo.git",
        )

        # Different protocols should match
        assert loader._urls_match(
            "https://github.com/test/repo.git",
            "http://github.com/test/repo.git",
        )

        # SSH and HTTPS should match
        assert loader._urls_match(
            "git@github.com:test/repo.git",
            "https://github.com/test/repo.git",
        )

        # With and without .git should match
        assert loader._urls_match(
            "https://github.com/test/repo.git",
            "https://github.com/test/repo",
        )

        # Different repos should not match
        assert not loader._urls_match(
            "https://github.com/test/repo1.git",
            "https://github.com/test/repo2.git",
        )

    def test_find_agent_config(self, tmp_path):
        """Test finding agent config in repositories."""
        # Create test repository structure
        repo1_path = tmp_path / "repo1"
        agents_dir = repo1_path / "agents"
        agents_dir.mkdir(parents=True)

        agent1_dir = agents_dir / "agent1"
        agent1_dir.mkdir()
        (agent1_dir / "config.yaml").write_text(yaml.dump({"name": "agent1"}))

        # Create loader with mock repos
        config_file = tmp_path / "repos.json"
        config_data = [
            {
                "name": "repo1",
                "url": "https://github.com/test/repo1.git",
                "clone_path": str(repo1_path),
            },
        ]
        config_file.write_text(json.dumps(config_data))

        loader = AgentConfigLoader(repos_config_path=str(config_file))

        # Find existing agent
        config_path = loader.find_agent_config("agent1")
        assert config_path is not None
        assert config_path.name == "config.yaml"

        # Find non-existent agent
        config_path = loader.find_agent_config("nonexistent")
        assert config_path is None

    def test_load_agent_config(self, tmp_path):
        """Test loading agent configuration."""
        # Create test repository structure
        repo_path = tmp_path / "repo"
        agents_dir = repo_path / "agents"
        agents_dir.mkdir(parents=True)

        agent_dir = agents_dir / "test-agent"
        agent_dir.mkdir()

        config_data = {
            "name": "test-agent",
            "display_name": "Test Agent",
            "type": "claude-code",
            "brain": {"model": "claude-sonnet-4"},
        }
        (agent_dir / "config.yaml").write_text(yaml.dump(config_data))
        (agent_dir / "system-prompt.md").write_text("You are helpful.")

        # Create loader
        config_file = tmp_path / "repos.json"
        repos_data = [
            {
                "name": "repo",
                "url": "https://github.com/test/repo.git",
                "clone_path": str(repo_path),
                "branch": "main",
            },
        ]
        config_file.write_text(json.dumps(repos_data))

        loader = AgentConfigLoader(repos_config_path=str(config_file))

        # Load agent config
        config = loader.load_agent_config("test-agent")

        assert config is not None
        assert config.name == "test-agent"
        assert config.display_name == "Test Agent"
        assert config.type == "claude-code"
        assert config.brain["model"] == "claude-sonnet-4"
        assert config.system_prompt == "You are helpful."
        assert config.config_source == "https://github.com/test/repo.git"
        assert config.config_branch == "main"

    def test_list_agents(self, tmp_path):
        """Test listing all agents in all repositories."""
        # Create test repository with multiple agents
        repo_path = tmp_path / "repo"
        agents_dir = repo_path / "agents"
        agents_dir.mkdir(parents=True)

        for i in range(3):
            agent_dir = agents_dir / f"agent{i}"
            agent_dir.mkdir()
            (agent_dir / "config.yaml").write_text(yaml.dump({"name": f"agent{i}"}))

        # Create loader
        config_file = tmp_path / "repos.json"
        repos_data = [
            {
                "name": "repo",
                "url": "https://github.com/test/repo.git",
                "clone_path": str(repo_path),
            },
        ]
        config_file.write_text(json.dumps(repos_data))

        loader = AgentConfigLoader(repos_config_path=str(config_file))

        agents = loader.list_agents()

        assert "repo" in agents
        assert set(agents["repo"]) == {"agent0", "agent1", "agent2"}

    def test_cache_invalidation(self, tmp_path):
        """Test that cache is cleared after refresh."""
        repo_path = tmp_path / "repo"
        agents_dir = repo_path / "agents"
        agents_dir.mkdir(parents=True)

        agent_dir = agents_dir / "cached-agent"
        agent_dir.mkdir()
        (agent_dir / "config.yaml").write_text(yaml.dump({"name": "cached-agent"}))

        config_file = tmp_path / "repos.json"
        repos_data = [
            {
                "name": "repo",
                "url": "https://github.com/test/repo.git",
                "clone_path": str(repo_path),
            },
        ]
        config_file.write_text(json.dumps(repos_data))

        loader = AgentConfigLoader(repos_config_path=str(config_file))

        # Load config (should be cached)
        config1 = loader.load_agent_config("cached-agent")
        assert len(loader.config_cache) == 1

        # Refresh (should clear cache)
        with patch.object(loader.git_manager, 'clone_or_pull_all', return_value={"repo": True}):
            loader.refresh_all_repos()

        assert len(loader.config_cache) == 0


class TestUtilityFunctions:
    """Test utility functions."""

    def test_load_repos_config(self, tmp_path):
        """Test loading repos config from JSON file."""
        config_file = tmp_path / "repos.json"
        config_data = [
            {"name": "repo1", "url": "https://github.com/test/repo1.git"},
            {"name": "repo2", "url": "https://github.com/test/repo2.git"},
        ]
        config_file.write_text(json.dumps(config_data))

        repos = load_repos_config(str(config_file))

        assert len(repos) == 2
        assert repos[0]["name"] == "repo1"
        assert repos[1]["url"] == "https://github.com/test/repo2.git"
