"""
Tests for agent registration script.

Tests cover:
- Agent configuration validation, driven by on-disk config.yaml /
  system-prompt.md fixtures covering every documented agent type
  (docs/agent-registration-guide.md, "Valid Agent Types")
- Agent config loading from YAML
- Git repository scanning (agents/ layout, malformed configs)
- Multi-repo handling (repos-file parsing, multi-repo validate-only runs
  against real local git remotes)
- Hub registration API calls against a stub Hub server (success,
  already-registered idempotency, auth failure) over real HTTP
- OpenBao key delivery contract (via a stub key store)
- OpenBao key delivery and reference-only reports
- Validation report generation
"""

import json
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests
import yaml

# Import the modules to test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import register_agents
from register_agents import (
    AgentConfig,
    AgentValidationReport,
    ValidationResult,
    GitRepository,
    ConfigValidator,
    AgentRegistrar,
    RepoConfig,
    load_repos_config,
    get_git_info,
    generate_validation_report,
    main,
)
from openbao_store import OpenBaoDeliveryError, OpenBaoReference

# Directory holding the config.yaml / system-prompt.md fixtures used by the
# validator, repo-scanning and multi-repo tests below.
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# The documented agent types (docs/agent-registration-guide.md,
# "Valid Agent Types"). Kept here as an explicit tripwire: the validator's
# accepted set and the documentation must not drift apart silently.
DOCUMENTED_AGENT_TYPES = {
    "claude-code",
    "goose",
    "aider",
    "opencode",
    "native",
    "claude",
}


def load_fixture(category: str, name: str):
    """Load a fixture's config dict and optional system prompt."""
    agent_dir = FIXTURES_DIR / category / name
    config = yaml.safe_load((agent_dir / "config.yaml").read_text())
    prompt_file = agent_dir / "system-prompt.md"
    prompt = prompt_file.read_text() if prompt_file.exists() else None
    return config, prompt


def fixture_cases(category: str):
    """Sorted fixture directory names under the given category."""
    return sorted(
        p.name for p in (FIXTURES_DIR / category).iterdir() if p.is_dir()
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


class StubKeyStore:
    """In-memory stand-in for OpenBaoClient.

    Records delivered keys so tests can assert exactly what was handed
    to the store without any OpenBao (or secret material) touching disk.
    """

    def __init__(self):
        self.delivered = []  # (agent_name, api_key) tuples

    def agent_ref(self, agent_name: str) -> OpenBaoReference:
        return OpenBaoReference(
            mount="secret",
            path=f"ardenone-cluster/botburrow/agents/{agent_name}",
        )

    def deliver(self, agent_name: str, api_key: str) -> OpenBaoReference:
        self.delivered.append((agent_name, api_key))
        return self.agent_ref(agent_name)


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

        key_store = Mock()
        key_store.deliver.return_value = OpenBaoReference(
            mount="secret",
            path="ardenone-cluster/botburrow/agents/test-agent",
        )

        registrar = AgentRegistrar(
            hub_url="https://botburrow.example.com",
            admin_key="admin-key",
            key_store=key_store,
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
        assert result["api_key_ref"] == "secret/ardenone-cluster/botburrow/agents/test-agent"
        assert "api_key" not in result
        key_store.deliver.assert_called_once_with("test-agent", "botburrow_agent_abc123")

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
        assert result["api_key_ref"] == "secret/ardenone-cluster/botburrow/agents/test-agent"
        assert "api_key" not in result

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


# ---------------------------------------------------------------------------
# Fixture-driven validation coverage
#
# scripts/tests/fixtures/ holds on-disk config.yaml / system-prompt.md
# pairs: one per documented agent type under valid/, plus invalid/ and
# warning/ cases. FIXTURE_EXPECTATIONS states, per fixture, the one
# message the validator must produce. A fixture directory with no entry
# here fails test_every_fixture_has_an_expectation, so a new fixture
# cannot be added without stating what it proves, and a validator change
# that breaks an expectation fails instead of regressing silently.
# ---------------------------------------------------------------------------

FIXTURE_EXPECTATIONS = {
    "invalid": {
        "Invalid_Agent": "must be lowercase",
        "bad-max-tokens": "max_tokens",
        "bad-temperature": "temperature",
        "mcp-server-missing-command": "missing 'command'",
        "mcp-server-missing-name": "missing 'name'",
        "mcp-server-not-a-dict": "must be a dictionary",
        "mcp-servers-not-a-list": "mcp_servers must be a list",
        "negative-max-daily-comments": "max_daily_comments",
        "negative-max-daily-posts": "max_daily_posts",
        "shell-commands-not-a-list": "allowed_commands must be a list",
    },
    "valid": {
        # name -> the documented agent type the fixture must carry
        "aider-agent": "aider",
        "claude-agent": "claude",
        "claude-code-agent": "claude-code",
        "goose-agent": "goose",
        "native-agent": "native",
        "opencode-agent": "opencode",
    },
    "warning": {
        "brain-minimal": "No brain configuration found",
        "brain-no-model-or-provider": "missing 'model' or 'provider'",
        "unknown-agent-type": "Unknown agent type",
        "unknown-capability": "Unknown capability type",
        "unknown-interest": "Unknown interest type",
    },
}


class TestFixtureDrivenValidation:
    """Validate every on-disk fixture through ConfigValidator."""

    def test_every_fixture_has_an_expectation(self):
        """A fixture without a stated expectation means a silent no-op test."""
        for category in ("invalid", "valid", "warning"):
            for name in fixture_cases(category):
                assert name in FIXTURE_EXPECTATIONS[category], (
                    f"fixtures/{category}/{name} has no entry in "
                    "FIXTURE_EXPECTATIONS; state what it proves"
                )

    def test_validator_types_match_documented_types(self):
        """The validator's accepted set must not drift from the docs."""
        assert ConfigValidator.VALID_AGENT_TYPES == DOCUMENTED_AGENT_TYPES

    def test_valid_fixtures_cover_every_documented_type(self):
        """Every documented agent type has a valid fixture."""
        assert set(FIXTURE_EXPECTATIONS["valid"].values()) == (
            DOCUMENTED_AGENT_TYPES
        )

    @pytest.mark.parametrize("name", sorted(FIXTURE_EXPECTATIONS["valid"]))
    def test_valid_fixtures_validate_clean(self, name):
        config, prompt = load_fixture("valid", name)
        assert config.get("type") == FIXTURE_EXPECTATIONS["valid"][name]
        result = ConfigValidator().validate_agent(name, config, prompt)
        assert result.errors == [], result.errors
        assert result.warnings == [], result.warnings
        assert result.is_valid

    @pytest.mark.parametrize(
        "name,expected", sorted(FIXTURE_EXPECTATIONS["invalid"].items())
    )
    def test_invalid_fixtures_fail_with_expected_error(self, name, expected):
        config, prompt = load_fixture("invalid", name)
        result = ConfigValidator().validate_agent(name, config, prompt)
        assert not result.is_valid
        assert any(expected in error for error in result.errors), result.errors

    @pytest.mark.parametrize(
        "name,expected", sorted(FIXTURE_EXPECTATIONS["warning"].items())
    )
    def test_warning_fixtures_pass_with_expected_warning(self, name, expected):
        config, prompt = load_fixture("warning", name)
        result = ConfigValidator().validate_agent(name, config, prompt)
        assert result.is_valid
        assert result.errors == [], result.errors
        assert any(expected in warning for warning in result.warnings), (
            result.warnings
        )


# ---------------------------------------------------------------------------
# Repo scanning over a tree assembled from the fixtures
# ---------------------------------------------------------------------------


class TestRepoScanningWithFixtures:
    """GitRepository.get_agents against realistic agents/ trees."""

    @staticmethod
    def _repo_over(tmp_path, include=()):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        for name in include:
            shutil.copytree(FIXTURES_DIR / "valid" / name, agents_dir / name)
        repo = GitRepository(url="test", branch="main")
        repo.repo_path = tmp_path
        return repo

    def test_finds_fixture_agents_and_prompts(self, tmp_path):
        repo = self._repo_over(tmp_path, ["native-agent", "goose-agent"])

        agents = repo.get_agents()

        assert {config["name"] for _, config, _ in agents} == {
            "native-agent",
            "goose-agent",
        }
        by_name = {config["name"]: (config, prompt) for _, config, prompt in agents}
        assert by_name["native-agent"][1] == (
            FIXTURES_DIR / "valid/native-agent/system-prompt.md"
        ).read_text()
        assert by_name["goose-agent"][1] == (
            FIXTURES_DIR / "valid/goose-agent/system-prompt.md"
        ).read_text()

    def test_skips_dirs_without_config_and_non_dirs(self, tmp_path):
        repo = self._repo_over(tmp_path, ["native-agent"])
        (tmp_path / "agents" / "prompt-only").mkdir()  # no config.yaml
        (tmp_path / "agents" / "notes.txt").write_text("not an agent dir")

        agents = repo.get_agents()

        assert [config["name"] for _, config, _ in agents] == ["native-agent"]

    def test_skips_malformed_config_without_raising(self, tmp_path):
        repo = self._repo_over(tmp_path, ["native-agent"])
        broken = tmp_path / "agents" / "broken"
        broken.mkdir()
        (broken / "config.yaml").write_text("brain: [unclosed")

        agents = repo.get_agents()

        assert [config["name"] for _, config, _ in agents] == ["native-agent"]


# ---------------------------------------------------------------------------
# Multi-repo handling
# ---------------------------------------------------------------------------


def build_local_agent_repo(path, agent_names):
    """git-init a local repo under path with agents/ copied from fixtures.

    Fixture names are looked up in valid/ first, then invalid/, so a mix
    like ["native-agent", "Invalid_Agent"] builds a repo with one good and
    one bad agent.
    """
    path.mkdir(parents=True)
    agents_dir = path / "agents"
    agents_dir.mkdir()
    for name in agent_names:
        for category in ("valid", "invalid", "warning"):
            source = FIXTURES_DIR / category / name
            if source.is_dir():
                shutil.copytree(source, agents_dir / name)
                break
        else:
            raise FileNotFoundError(f"no fixture named {name}")
    git = ["git", "-c", "user.email=github@jedarden.com",
           "-c", "user.name=jedarden"]
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True,
                   capture_output=True)
    subprocess.run(["git", "add", "agents"], cwd=path, check=True,
                   capture_output=True)
    subprocess.run(git + ["commit", "-m", "fixture agents"], cwd=path,
                   check=True, capture_output=True)
    return path


class TestMultiRepoHandling:
    """Multi-repo behaviour: repos-file parsing and validate-only runs."""

    def test_load_repos_config_mixed_entries(self, tmp_path):
        config_file = tmp_path / "repos.json"
        config_file.write_text(json.dumps([
            {"name": "configured", "url": "https://example.com/a.git",
             "branch": "release", "auth_type": "token",
             "auth_secret": "a-token"},
            "https://example.com/b.git",
            42,  # not a repo config: skipped with a warning
        ]))

        repos = load_repos_config(str(config_file))

        assert [r.name for r in repos] == ["configured", "unknown"]
        assert repos[0].branch == "release"
        assert repos[0].auth_type == "token"
        assert repos[1].url == "https://example.com/b.git"
        assert repos[1].branch == "main"

    def run_main(self, monkeypatch, cwd, argv):
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(sys, "argv", ["register_agents.py"] + argv)
        return main()

    def test_validate_only_across_two_repos(self, tmp_path, monkeypatch):
        repo_a = build_local_agent_repo(tmp_path / "repo-a",
                                        ["native-agent", "goose-agent"])
        repo_b = build_local_agent_repo(tmp_path / "repo-b",
                                        ["claude-code-agent"])
        report = tmp_path / "report.json"

        rc = self.run_main(monkeypatch, tmp_path, [
            "--repo", str(repo_a), "--repo", str(repo_b),
            "--validate-only", "--output-report", str(report),
        ])

        assert rc == 0
        data = json.loads(report.read_text())
        assert data["total_agents"] == 3
        assert data["invalid_agents"] == 0
        sources = {agent["config_source"] for agent in data["agents"]}
        assert sources == {str(repo_a), str(repo_b)}
        assert all(agent["valid"] for agent in data["agents"])

    def test_invalid_agent_fails_strict_validate_only(self, tmp_path, monkeypatch):
        repo = build_local_agent_repo(tmp_path / "repo",
                                      ["native-agent", "Invalid_Agent"])
        report = tmp_path / "report.json"

        rc = self.run_main(monkeypatch, tmp_path, [
            "--repo", str(repo), "--validate-only", "--strict",
            "--output-report", str(report),
        ])

        assert rc == 1
        data = json.loads(report.read_text())
        assert data["invalid_agents"] == 1
        invalid = next(a for a in data["agents"] if not a["valid"])
        assert invalid["name"] == "Invalid_Agent"

    def test_invalid_agent_passes_gate_without_strict(self, tmp_path, monkeypatch):
        # Pins current semantics: without --strict, validation errors are
        # reported but do not fail the run. If the gate should hard-fail
        # CI, change the script and this test together.
        repo = build_local_agent_repo(tmp_path / "repo",
                                      ["native-agent", "Invalid_Agent"])
        report = tmp_path / "report.json"

        rc = self.run_main(monkeypatch, tmp_path, [
            "--repo", str(repo), "--validate-only",
            "--output-report", str(report),
        ])

        assert rc == 0
        data = json.loads(report.read_text())
        assert data["invalid_agents"] == 1
        assert data["valid_agents"] == 1


# ---------------------------------------------------------------------------
# Hub registration against a real HTTP stub Hub
# ---------------------------------------------------------------------------


class _StubHubHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for the Hub: /api/v1/health + /api/v1/agents/register.

    State lives on the ThreadingHTTPServer instance (admin_key, registered,
    requests, omit_api_key) so each test configures its own server.
    """

    def _json(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self):
        return self.headers.get("X-Admin-Key") == self.server.admin_key

    def do_GET(self):
        if self.path != "/api/v1/health":
            self._json(404, {"detail": "not found"})
        elif self._authorized():
            self._json(200, {"status": "ok"})
        else:
            self._json(401, {"detail": "invalid admin key"})

    def do_POST(self):
        if self.path != "/api/v1/agents/register":
            self._json(404, {"detail": "not found"})
            return
        if not self._authorized():
            self._json(401, {"detail": "invalid admin key"})
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        self.server.requests.append(payload)
        name = payload.get("name", "")
        if name in self.server.registered:
            # The Hub keeps only a hash, so it can never re-send the key.
            self._json(200, {"id": f"agent-{name}", "name": name,
                             "api_key": "(unchanged)"})
            return
        self.server.registered.add(name)
        body = {"id": f"agent-{name}", "name": name,
                "type": payload.get("type"),
                "config_source": payload.get("config_source"),
                "created_at": "2026-09-16T00:00:00Z"}
        if not self.server.omit_api_key:
            body["api_key"] = f"botburrow_agent_{name}"
        self._json(201, body)

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass


@pytest.fixture
def stub_hub():
    """A stub Hub on a random local port; tests talk to it over real HTTP."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHubHandler)
    server.admin_key = "test-admin-key"
    server.registered = set()
    server.requests = []
    server.omit_api_key = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def registrar_for(server, admin_key=None, key_store=...):
    """An AgentRegistrar pointed at the stub Hub.

    key_store defaults to a fresh StubKeyStore; pass None explicitly to
    test the no-store refusal path.
    """
    return AgentRegistrar(
        hub_url=f"http://127.0.0.1:{server.server_address[1]}",
        admin_key=server.admin_key if admin_key is None else admin_key,
        key_store=StubKeyStore() if key_store is ... else key_store,
    )


class TestStubHubRegistration:
    """Integration tests for AgentRegistrar over real HTTP."""

    def test_register_success_delivers_key_and_sanitizes_response(self, stub_hub):
        key_store = StubKeyStore()
        registrar = registrar_for(stub_hub, key_store=key_store)
        config = AgentConfig(name="test-agent", display_name="Test Agent",
                             type="claude-code")

        result = registrar.register_agent(
            config,
            config_source="https://repos.example/agents.git",
            config_path="agents/test-agent",
        )

        assert result["name"] == "test-agent"
        assert result["type"] == "claude-code"
        assert result["api_key_ref"] == (
            "secret/ardenone-cluster/botburrow/agents/test-agent"
        )
        assert "api_key" not in result  # plaintext consumed, never returned
        assert key_store.delivered == [
            ("test-agent", "botburrow_agent_test-agent")
        ]
        sent = stub_hub.requests[0]
        assert sent["type"] == "claude-code"
        assert sent["config_source"] == "https://repos.example/agents.git"
        assert sent["config_path"] == "agents/test-agent"
        assert sent["config_branch"] == "main"

    def test_already_registered_is_idempotent(self, stub_hub):
        stub_hub.registered.add("test-agent")
        key_store = StubKeyStore()
        registrar = registrar_for(stub_hub, key_store=key_store)

        result = registrar.register_agent(
            AgentConfig(name="test-agent"),
            config_source="https://repos.example/agents.git",
            config_path="agents/test-agent",
        )

        assert result["api_key_delivery"] == "unchanged"
        assert result["api_key_ref"] == (
            "secret/ardenone-cluster/botburrow/agents/test-agent"
        )
        assert key_store.delivered == []  # nothing delivered: no key returned

    def test_auth_failure_is_rejected(self, stub_hub):
        registrar = registrar_for(stub_hub, admin_key="wrong-key")

        with pytest.raises(requests.exceptions.HTTPError) as excinfo:
            registrar.register_agent(
                AgentConfig(name="test-agent"),
                config_source="s", config_path="agents/test-agent",
            )

        assert excinfo.value.response.status_code == 401
        assert stub_hub.requests == []  # nothing registered

    def test_response_without_key_is_a_delivery_error(self, stub_hub):
        stub_hub.omit_api_key = True
        registrar = registrar_for(stub_hub)

        with pytest.raises(OpenBaoDeliveryError, match="did not return an API key"):
            registrar.register_agent(
                AgentConfig(name="test-agent"),
                config_source="s", config_path="agents/test-agent",
            )

    def test_key_without_key_store_refuses_to_drop_credential(self, stub_hub):
        registrar = registrar_for(stub_hub, key_store=None)

        with pytest.raises(OpenBaoDeliveryError, match="no OpenBao key store"):
            registrar.register_agent(
                AgentConfig(name="test-agent"),
                config_source="s", config_path="agents/test-agent",
            )

    def test_check_hub_connection_over_http(self, stub_hub):
        assert registrar_for(stub_hub).check_hub_connection() is True
        assert registrar_for(stub_hub, admin_key="wrong-key") \
            .check_hub_connection() is False


class TestEndToEndRegistration:
    """main() across two local repos, a stub Hub and a stub key store."""

    def test_registration_flow_across_repos(self, tmp_path, monkeypatch, stub_hub):
        repo_a = build_local_agent_repo(tmp_path / "repo-a", ["native-agent"])
        repo_b = build_local_agent_repo(tmp_path / "repo-b",
                                        ["goose-agent", "claude-code-agent"])
        key_store = StubKeyStore()
        monkeypatch.setattr(register_agents, "OpenBaoClient",
                            lambda **kwargs: key_store)
        monkeypatch.setenv("HUB_ADMIN_KEY", stub_hub.admin_key)

        argv = ["--repo", str(repo_a), "--repo", str(repo_b),
                "--hub-url", f"http://127.0.0.1:{stub_hub.server_address[1]}",
                "--output-report", str(tmp_path / "report.json")]
        assert self._run(monkeypatch, tmp_path, argv) == 0
        assert sorted(name for name, _ in key_store.delivered) == [
            "claude-code-agent", "goose-agent", "native-agent",
        ]
        results = json.loads((tmp_path / "registration-results.json").read_text())
        assert {agent["name"] for agent in results["agents"]} == {
            "native-agent", "goose-agent", "claude-code-agent",
        }
        assert all(
            agent["api_key_ref"] ==
            f"secret/ardenone-cluster/botburrow/agents/{agent['name']}"
            for agent in results["agents"]
        )
        assert {repo["url"] for repo in results["repositories"]} == {
            str(repo_a), str(repo_b),
        }

        # Re-running is idempotent: the Hub re-registers without re-issuing
        # keys, so no new delivery happens and the run still succeeds.
        assert self._run(monkeypatch, tmp_path, argv) == 0
        assert len(key_store.delivered) == 3

    def test_invalid_agent_still_registered_without_strict(
        self, tmp_path, monkeypatch, stub_hub
    ):
        repo = build_local_agent_repo(
            tmp_path / "repo", ["goose-agent", "Invalid_Agent"])
        key_store = StubKeyStore()
        monkeypatch.setattr(register_agents, "OpenBaoClient",
                            lambda **kwargs: key_store)
        monkeypatch.setenv("HUB_ADMIN_KEY", stub_hub.admin_key)

        rc = self._run(monkeypatch, tmp_path, [
            "--repo", str(repo),
            "--hub-url", f"http://127.0.0.1:{stub_hub.server_address[1]}",
            "--output-report", str(tmp_path / "report.json"),
        ])

        # Pins current semantics: without --strict, validation errors are
        # logged but the agent is still registered. If invalid agents
        # should never reach the Hub, change the script and this test
        # together.
        assert rc == 0
        assert sorted(name for name, _ in key_store.delivered) == [
            "Invalid_Agent", "goose-agent",
        ]

    def test_strict_skips_invalid_agent_and_fails_run(
        self, tmp_path, monkeypatch, stub_hub
    ):
        repo = build_local_agent_repo(
            tmp_path / "repo", ["goose-agent", "Invalid_Agent"])
        key_store = StubKeyStore()
        monkeypatch.setattr(register_agents, "OpenBaoClient",
                            lambda **kwargs: key_store)
        monkeypatch.setenv("HUB_ADMIN_KEY", stub_hub.admin_key)

        rc = self._run(monkeypatch, tmp_path, [
            "--repo", str(repo), "--strict",
            "--hub-url", f"http://127.0.0.1:{stub_hub.server_address[1]}",
            "--output-report", str(tmp_path / "report.json"),
        ])

        # With --strict the invalid agent is skipped and the run fails...
        assert rc == 1
        # ...but the valid agent from the same repo was still registered.
        assert [name for name, _ in key_store.delivered] == ["goose-agent"]

    @staticmethod
    def _run(monkeypatch, cwd, argv):
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(sys, "argv", ["register_agents.py"] + argv)
        return main()
