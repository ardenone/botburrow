"""
Tests for agent registration script.

Tests cover:
- Agent configuration validation
- Agent config loading from YAML
- Git repository operations
- Hub registration API calls
- Secret manifest generation
- Validation report generation
"""

import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest
import yaml

# Import the modules to test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from register_agents import (
    AgentConfig,
    AgentValidationReport,
    ValidationResult,
    GitRepository,
    ConfigValidator,
    AgentRegistrar,
    RepoConfig,
    generate_secret_template,
    generate_sealed_secret,
    load_repos_config,
    get_git_info,
    generate_validation_report,
)


class TestAgentConfig:
    """Test AgentConfig dataclass."""

    def test_from_dict_minimal(self):
        """Test creating config from minimal dictionary."""
        data = {"name": "test-agent"}
        config = AgentConfig.from_dict(data)
        assert config.name == "test-agent"
        assert config.type == "native"  # default
        assert config.brain == {}  # default

    def test_from_dict_full(self):
        """Test creating config from full dictionary."""
        data = {
            "name": "test-agent",
            "display_name": "Test Agent",
            "description": "A test agent",
            "type": "claude-code",
            "brain": {"model": "claude-sonnet-4", "max_tokens": 4096},
            "capabilities": {"shell": {"enabled": True}},
        }
        config = AgentConfig.from_dict(data, system_prompt="You are a helpful assistant.")
        assert config.name == "test-agent"
        assert config.display_name == "Test Agent"
        assert config.type == "claude-code"
        assert config.brain["model"] == "claude-sonnet-4"
        assert config.system_prompt == "You are a helpful assistant."


class TestValidationResult:
    """Test ValidationResult dataclass."""

    def test_add_error(self):
        """Test adding an error marks result as invalid."""
        result = ValidationResult(is_valid=True, agent_name="test")
        result.add_error("Something is wrong")
        assert not result.is_valid
        assert "Something is wrong" in result.errors

    def test_add_warning(self):
        """Test adding a warning doesn't affect validity."""
        result = ValidationResult(is_valid=True, agent_name="test")
        result.add_warning("This might be a problem")
        assert result.is_valid
        assert "This might be a problem" in result.warnings


class TestConfigValidator:
    """Test ConfigValidator class."""

    def test_validate_agent_valid(self):
        """Test validation of a valid agent config."""
        validator = ConfigValidator()
        config = {
            "name": "test-agent",
            "type": "claude-code",
            "brain": {"model": "claude-sonnet-4", "max_tokens": 4096, "temperature": 0.7},
            "capabilities": {
                "mcp_servers": [
                    {"name": "git", "command": "mcp-server-git"}
                ]
            },
        }
        system_prompt = "You are a test agent."

        result = validator.validate_agent("test-agent", config, system_prompt)
        assert result.is_valid
        assert len(result.errors) == 0

    def test_validate_agent_invalid_name(self):
        """Test validation rejects invalid agent names."""
        validator = ConfigValidator()
        config = {"name": "Invalid_Name"}

        result = validator.validate_agent("Invalid_Name", config, None)
        assert not result.is_valid
        assert any("must be lowercase" in e for e in result.errors)

    def test_validate_agent_missing_system_prompt(self):
        """Test validation warns about missing system prompt."""
        validator = ConfigValidator()
        config = {"name": "test-agent"}

        result = validator.validate_agent("test-agent", config, None)
        assert any("system-prompt" in w for w in result.warnings)

    def test_validate_agent_invalid_temperature(self):
        """Test validation rejects invalid temperature values."""
        validator = ConfigValidator()
        config = {
            "name": "test-agent",
            "brain": {"temperature": 3.0}  # > 2.0
        }

        result = validator.validate_agent("test-agent", config, None)
        assert not result.is_valid
        assert any("temperature" in e for e in result.errors)

    def test_validate_agent_invalid_max_tokens(self):
        """Test validation rejects invalid max_tokens."""
        validator = ConfigValidator()
        config = {
            "name": "test-agent",
            "brain": {"max_tokens": -100}
        }

        result = validator.validate_agent("test-agent", config, None)
        assert not result.is_valid
        assert any("max_tokens" in e for e in result.errors)

    def test_validate_agent_invalid_mcp_server(self):
        """Test validation rejects invalid MCP server configuration."""
        validator = ConfigValidator()
        config = {
            "name": "test-agent",
            "capabilities": {
                "mcp_servers": [
                    {"name": "test"}  # missing command
                ]
            }
        }

        result = validator.validate_agent("test-agent", config, None)
        assert not result.is_valid
        assert any("MCP server" in e for e in result.errors)

    def test_validate_agent_unknown_type(self):
        """Test validation warns about unknown agent types."""
        validator = ConfigValidator()
        config = {
            "name": "test-agent",
            "type": "unknown-type"
        }

        result = validator.validate_agent("test-agent", config, None)
        assert any("Unknown agent type" in w for w in result.warnings)

    def test_validate_agent_invalid_behavior_limits(self):
        """Test validation rejects invalid behavior limits."""
        validator = ConfigValidator()
        config = {
            "name": "test-agent",
            "behavior": {
                "limits": {
                    "max_daily_posts": -5  # negative
                }
            }
        }

        result = validator.validate_agent("test-agent", config, None)
        assert not result.is_valid
        assert any("max_daily_posts" in e for e in result.errors)

    def test_strict_mode_warnings_as_errors(self):
        """Test strict mode doesn't convert warnings to errors directly."""
        validator = ConfigValidator(strict=True)
        config = {"name": "test-agent"}

        result = validator.validate_agent("test-agent", config, None)
        # Warnings are still warnings, caller decides what to do
        assert len(result.warnings) > 0


class TestAgentValidationReport:
    """Test AgentValidationReport class."""

    def test_to_json(self):
        """Test converting report to JSON."""
        report = AgentValidationReport(
            timestamp="2024-01-01T00:00:00",
            repo_url="https://github.com/test/repo.git",
            branch="main",
            commit_sha="abc123",
            total_agents=2,
            valid_agents=1,
            invalid_agents=1,
            warnings=3,
            agents=[{"name": "agent1", "valid": True}],
            summary="1 valid, 1 invalid",
        )

        json_str = report.to_json()
        data = json.loads(json_str)
        assert data["total_agents"] == 2
        assert data["valid_agents"] == 1

    def test_to_markdown(self):
        """Test converting report to Markdown."""
        report = AgentValidationReport(
            timestamp="2024-01-01T00:00:00",
            repo_url="https://github.com/test/repo.git",
            branch="main",
            commit_sha="abc123",
            total_agents=2,
            valid_agents=2,
            invalid_agents=0,
            warnings=0,
            agents=[],
            summary="All agents validated successfully.",
        )

        md = report.to_markdown()
        assert "# Agent Registration Validation Report" in md
        assert "https://github.com/test/repo.git" in md
        assert "Total Agents | 2" in md
        assert "All agents validated successfully" in md

    def test_to_markdown_with_errors(self):
        """Test Markdown report includes validation errors."""
        report = AgentValidationReport(
            timestamp="2024-01-01T00:00:00",
            repo_url="https://github.com/test/repo.git",
            branch="main",
            commit_sha="abc123",
            total_agents=1,
            valid_agents=0,
            invalid_agents=1,
            warnings=0,
            agents=[{
                "name": "bad-agent",
                "valid": False,
                "errors": ["Missing required field"]
            }],
            summary="1 agent failed validation.",
        )

        md = report.to_markdown()
        assert "### ❌ Validation Errors" in md
        assert "bad-agent" in md
        assert "Missing required field" in md


class TestGitRepository:
    """Test GitRepository class."""

    def test_init(self):
        """Test GitRepository initialization."""
        repo = GitRepository(
            url="https://github.com/test/repo.git",
            branch="main",
            clone_depth=1,
            timeout=30,
        )
        assert repo.url == "https://github.com/test/repo.git"
        assert repo.branch == "main"
        assert repo.clone_depth == 1
        assert repo.timeout == 30

    @patch('subprocess.run')
    def test_clone_success(self, mock_run):
        """Test successful repository clone."""
        mock_run.return_value = Mock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = GitRepository(
                url="https://github.com/test/repo.git",
                branch="main",
            )
            repo._temp_dir = tempfile.TemporaryDirectory(prefix="test_agent_")
            repo.repo_path = Path(repo._temp_dir.name)

            # Should not raise
            repo._clone()

            # Verify git command was called correctly
            assert mock_run.called
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "git"
            assert "clone" in cmd
            assert "--depth" in cmd

    @patch('subprocess.run')
    def test_clone_failure(self, mock_run):
        """Test failed repository clone."""
        mock_run.return_value = Mock(
            returncode=1,
            stderr="fatal: repository not found"
        )

        repo = GitRepository(
            url="https://github.com/nonexistent/repo.git",
            branch="main",
        )

        with pytest.raises(RuntimeError, match="Git clone failed"):
            with repo:
                pass

    def test_get_agents_no_agents_dir(self, tmp_path):
        """Test get_agents when no agents directory exists."""
        # Create empty repo
        repo = GitRepository(url="test", branch="main")
        repo.repo_path = tmp_path

        agents = repo.get_agents()
        assert agents == []

    def test_get_agents_with_configs(self, tmp_path):
        """Test get_agents finds and loads agent configurations."""
        # Create agents directory structure
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        # Create agent 1
        agent1_dir = agents_dir / "agent1"
        agent1_dir.mkdir()
        (agent1_dir / "config.yaml").write_text(yaml.dump({
            "name": "agent1",
            "type": "claude-code"
        }))
        (agent1_dir / "system-prompt.md").write_text("You are agent1.")

        # Create agent 2
        agent2_dir = agents_dir / "agent2"
        agent2_dir.mkdir()
        (agent2_dir / "config.yaml").write_text(yaml.dump({
            "name": "agent2",
            "type": "native"
        }))

        repo = GitRepository(url="test", branch="main")
        repo.repo_path = tmp_path

        agents = repo.get_agents()
        assert len(agents) == 2

        # Find agents by name since directory order may vary
        agent1_data = next((a for a in agents if a[1].get("name") == "agent1"), None)
        agent2_data = next((a for a in agents if a[1].get("name") == "agent2"), None)

        assert agent1_data is not None
        assert agent2_data is not None

        # Check agent1
        _, config1, prompt1 = agent1_data
        assert config1["name"] == "agent1"
        assert prompt1 == "You are agent1."

        # Check agent2
        _, config2, prompt2 = agent2_data
        assert config2["name"] == "agent2"
        assert prompt2 is None


class TestAgentRegistrar:
    """Test AgentRegistrar class."""

    def test_init(self):
        """Test registrar initialization."""
        registrar = AgentRegistrar(
            hub_url="https://botburrow.example.com",
            admin_key="test-key",
            dry_run=False,
        )
        assert registrar.hub_url == "https://botburrow.example.com"
        assert registrar.admin_key == "test-key"
        assert not registrar.dry_run

    @patch('register_agents.AgentRegistrar._get_session')
    def test_register_agent_success(self, mock_session):
        """Test successful agent registration."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "uuid-123",
            "name": "test-agent",
            "api_key": "botburrow_agent_abc123",
        }
        mock_response.raise_for_status = Mock()
        mock_session.return_value.post.return_value = mock_response

        registrar = AgentRegistrar(
            hub_url="https://botburrow.example.com",
            admin_key="admin-key",
        )

        config = AgentConfig(
            name="test-agent",
            display_name="Test Agent",
            description="A test agent",
            type="claude-code",
        )

        result = registrar.register_agent(
            config,
            config_source="https://github.com/test/repo.git",
            config_path="agents/test-agent",
        )

        assert result["name"] == "test-agent"
        assert "api_key" in result

    def test_register_agent_dry_run(self):
        """Test dry run mode doesn't make API calls."""
        registrar = AgentRegistrar(
            hub_url="https://botburrow.example.com",
            admin_key="admin-key",
            dry_run=True,
        )

        config = AgentConfig(name="test-agent")

        result = registrar.register_agent(
            config,
            config_source="https://github.com/test/repo.git",
            config_path="agents/test-agent",
        )

        assert result["dry_run"] is True
        assert "api_key" in result

    @patch('register_agents.AgentRegistrar._get_session')
    def test_register_agent_failure(self, mock_session):
        """Test agent registration failure handling."""
        import requests
        mock_session.return_value.post.side_effect = (
            requests.exceptions.ConnectionError("Connection refused")
        )

        registrar = AgentRegistrar(
            hub_url="https://botburrow.example.com",
            admin_key="admin-key",
        )

        config = AgentConfig(name="test-agent")

        # The exception is re-raised as-is after logging
        with pytest.raises(requests.exceptions.ConnectionError):
            registrar.register_agent(
                config,
                config_source="https://github.com/test/repo.git",
                config_path="agents/test-agent",
            )

    def test_generate_api_key(self):
        """Test API key generation."""
        registrar = AgentRegistrar(
            hub_url="https://botburrow.example.com",
            admin_key="admin-key",
        )

        api_key = registrar._generate_api_key()
        assert api_key.startswith("botburrow_agent_")
        assert len(api_key) > len("botburrow_agent_")

    @patch('register_agents.AgentRegistrar._get_session')
    def test_check_hub_connection_success(self, mock_session):
        """Test successful Hub connection check."""
        mock_response = Mock(status_code=200)
        mock_session.return_value.get.return_value = mock_response

        registrar = AgentRegistrar(
            hub_url="https://botburrow.example.com",
            admin_key="admin-key",
        )

        assert registrar.check_hub_connection()

    @patch('register_agents.AgentRegistrar._get_session')
    def test_check_hub_connection_failure(self, mock_session):
        """Test failed Hub connection check."""
        import requests
        mock_session.return_value.get.side_effect = (
            requests.exceptions.ConnectionError()
        )

        registrar = AgentRegistrar(
            hub_url="https://botburrow.example.com",
            admin_key="admin-key",
        )

        assert not registrar.check_hub_connection()


class TestSecretGeneration:
    """Test secret manifest generation functions."""

    def test_generate_secret_template(self):
        """Test Kubernetes Secret template generation."""
        api_key = "botburrow_agent_abc123"
        agent_name = "test-agent"

        secret_yaml = generate_secret_template(api_key, agent_name)

        assert "apiVersion: v1" in secret_yaml
        assert "kind: Secret" in secret_yaml
        assert f"name: agent-{agent_name}" in secret_yaml
        assert "namespace: botburrow-agents" in secret_yaml
        assert "DO NOT COMMIT THIS FILE TO GIT" in secret_yaml
        assert "SealedSecrets" in secret_yaml

    def test_generate_secret_template_custom_namespace(self):
        """Test Secret template with custom namespace."""
        api_key = "botburrow_agent_abc123"
        agent_name = "test-agent"
        namespace = "custom-namespace"

        secret_yaml = generate_secret_template(api_key, agent_name, namespace)

        assert f"namespace: {namespace}" in secret_yaml

    @patch('subprocess.run')
    def test_generate_sealed_secret_success(self, mock_run):
        """Test successful SealedSecret generation."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="apiVersion: bitnami.com/v1alpha1\nkind: SealedSecret..."
        )

        result = generate_sealed_secret(
            api_key="botburrow_agent_abc123",
            agent_name="test-agent",
        )

        assert result is not None
        assert "SealedSecret" in result

    @patch('subprocess.run')
    def test_generate_sealed_secret_kubeseal_not_found(self, mock_run):
        """Test SealedSecret generation when kubeseal is not installed."""
        mock_run.side_effect = FileNotFoundError()

        result = generate_sealed_secret(
            api_key="botburrow_agent_abc123",
            agent_name="test-agent",
        )

        assert result is None


class TestUtilityFunctions:
    """Test utility functions."""

    def test_load_repos_config(self, tmp_path):
        """Test loading repository configuration from JSON file."""
        config_file = tmp_path / "repos.json"
        config_file.write_text(json.dumps([
            {"name": "test-repo", "url": "https://github.com/test/repo.git", "branch": "main"},
            {"name": "another-repo", "url": "https://github.com/test/another.git", "branch": "main"},
        ]))

        repos = load_repos_config(str(config_file))
        assert len(repos) == 2
        assert isinstance(repos[0], RepoConfig)
        assert repos[0].name == "test-repo"
        assert repos[0].url == "https://github.com/test/repo.git"
        assert repos[0].branch == "main"
        assert repos[1].name == "another-repo"

    def test_load_repos_config_simple_urls(self, tmp_path):
        """Test loading repository configuration with simple URL strings."""
        config_file = tmp_path / "repos.json"
        config_file.write_text(json.dumps([
            "https://github.com/test/repo.git",
            "https://github.com/test/another.git",
        ]))

        repos = load_repos_config(str(config_file))
        assert len(repos) == 2
        assert isinstance(repos[0], RepoConfig)
        assert repos[0].url == "https://github.com/test/repo.git"
        assert repos[0].branch == "main"
        assert repos[0].auth_type == "none"

    def test_load_repos_config_invalid_json(self, tmp_path):
        """Test loading invalid JSON configuration."""
        config_file = tmp_path / "repos.json"
        config_file.write_text("invalid json")

        with pytest.raises(json.JSONDecodeError):
            load_repos_config(str(config_file))

    @patch('subprocess.run')
    def test_get_git_info_success(self, mock_run):
        """Test getting git info from repository."""
        mock_run.side_effect = [
            Mock(stdout="abc123\n", returncode=0),  # rev-parse HEAD
            Mock(stdout="main\n", returncode=0),    # rev-parse --abbrev-ref HEAD
        ]

        commit_sha, branch = get_git_info()
        assert commit_sha == "abc123"
        assert branch == "main"

    @patch('subprocess.run')
    def test_get_git_info_failure(self, mock_run):
        """Test getting git info when git is not available."""
        mock_run.side_effect = FileNotFoundError()

        commit_sha, branch = get_git_info()
        assert commit_sha == "unknown"
        assert branch == "unknown"

    def test_generate_validation_report(self):
        """Test validation report generation."""
        agents_data = [
            {"name": "agent1", "valid": True, "errors": [], "warnings": []},
            {"name": "agent2", "valid": False, "errors": ["Missing field"], "warnings": ["No prompt"]},
        ]

        report = generate_validation_report(
            agents_data=agents_data,
            repo_url="https://github.com/test/repo.git",
            branch="main",
            commit_sha="abc123",
        )

        assert report.total_agents == 2
        assert report.valid_agents == 1
        assert report.invalid_agents == 1
        assert report.warnings == 1
        assert report.repo_url == "https://github.com/test/repo.git"
        assert report.branch == "main"
        assert report.commit_sha == "abc123"

    def test_generate_validation_report_all_valid(self):
        """Test validation report summary for all valid agents."""
        agents_data = [
            {"name": "agent1", "valid": True, "errors": [], "warnings": []},
            {"name": "agent2", "valid": True, "errors": [], "warnings": []},
        ]

        report = generate_validation_report(
            agents_data=agents_data,
            repo_url="https://github.com/test/repo.git",
            branch="main",
            commit_sha="abc123",
        )

        assert "All 2 agent(s) validated successfully" in report.summary

    def test_generate_validation_report_all_invalid(self):
        """Test validation report summary for all invalid agents."""
        agents_data = [
            {"name": "agent1", "valid": False, "errors": ["Bad"], "warnings": []},
            {"name": "agent2", "valid": False, "errors": ["Worse"], "warnings": []},
        ]

        report = generate_validation_report(
            agents_data=agents_data,
            repo_url="https://github.com/test/repo.git",
            branch="main",
            commit_sha="abc123",
        )

        assert "All 2 agent(s) failed validation" in report.summary
