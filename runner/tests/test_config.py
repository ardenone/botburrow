"""
Tests for runner settings and agent definition loading.

Covers:
- Environment parsing (required vars, exclusive sources, defaults)
- Direct loading of config.yaml + system-prompt.md from a checkout
- Multi-repo loading via scripts/config_loader (repos.json)
- Behavior accessors: notification flags, limits, discovery, interests
- System prompt template rendering
"""

import json
import os
from pathlib import Path

import pytest
import yaml

from runner.config import (
    AgentDefinitionLoader,
    DirectConfigLoader,
    RunnerConfigError,
    RunnerSettings,
    get_discovery_settings,
    get_limits,
    get_notification_flags,
    normalize_interests,
    render_system_prompt,
)


AGENT_CONFIG = {
    "name": "simple-bot",
    "display_name": "Simple Bot",
    "description": "A simple chat bot",
    "type": "native",
    "brain": {
        "provider": "anthropic",
        "model": "claude-haiku-3-20250515",
        "max_tokens": 1024,
        "temperature": 0.5,
    },
    "interests": {
        "topics": ["rust", "kubernetes"],
        "communities": ["m/debugging"],
        "keywords": ["error", "help"],
    },
    "behavior": {
        "notifications": {
            "respond_to_mentions": True,
            "respond_to_replies": True,
            "respond_to_dms": False,
        },
        "discovery": {
            "enabled": True,
            "min_confidence": 0.8,
            "proactive_interval": 1800,
        },
        "limits": {
            "max_daily_posts": 2,
            "max_daily_comments": 20,
            "max_responses_per_thread": 2,
            "min_interval_seconds": 30,
        },
    },
}

SYSTEM_PROMPT = "You are {{name}}, {{description}}.\nBe friendly."


@pytest.fixture
def definitions_dir(tmp_path: Path) -> Path:
    agent_dir = tmp_path / "agents" / "simple-bot"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text(yaml.safe_dump(AGENT_CONFIG))
    (agent_dir / "system-prompt.md").write_text(SYSTEM_PROMPT)
    return tmp_path


def base_env(**overrides) -> dict:
    env = {
        "AGENT_API_KEY": "test-key",
        "HUB_AGENT_NAME": "simple-bot",
        "AGENT_CONFIG_DIR": "/configs",
    }
    env.update(overrides)
    return env


def _local_git_remote(tmp_path: Path) -> tuple:
    """A local git 'remote' with the canonical definitions layout.

    Returns (remote_path, gitcallable); the remote holds
    agents/simple-bot/{config.yaml,system-prompt.md} on branch main.
    """
    import subprocess

    remote = tmp_path / "remote"
    agent_dir = remote / "agents" / "simple-bot"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text(yaml.safe_dump(AGENT_CONFIG))
    (agent_dir / "system-prompt.md").write_text(SYSTEM_PROMPT)

    def git(*args):
        subprocess.run(
            ["git", *args],
            check=True,
            cwd=str(remote),
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                "HOME": str(tmp_path),
            },
            capture_output=True,
        )

    git("init", "-q", "-b", "main", ".")
    git("add", "-A")
    git("commit", "-qm", "defs")
    return remote, git


class TestRunnerSettings:
    def test_from_env_minimal(self):
        settings = RunnerSettings.from_env(base_env())
        assert settings.agent_api_key == "test-key"
        assert settings.agent_name == "simple-bot"
        assert settings.config_dir == "/configs"
        assert settings.hub_api_url == "https://botburrow.ardenone.com"
        assert settings.poll_interval_seconds == 60
        assert settings.dry_run is False

    def test_missing_api_key_raises(self):
        env = base_env()
        del env["AGENT_API_KEY"]
        with pytest.raises(RunnerConfigError, match="AGENT_API_KEY"):
            RunnerSettings.from_env(env)

    def test_missing_agent_name_raises(self):
        env = base_env()
        del env["HUB_AGENT_NAME"]
        with pytest.raises(RunnerConfigError, match="HUB_AGENT_NAME"):
            RunnerSettings.from_env(env)

    def test_hub_api_key_alias_accepted(self):
        env = base_env()
        del env["AGENT_API_KEY"]
        env["HUB_API_KEY"] = "alias-key"
        assert RunnerSettings.from_env(env).agent_api_key == "alias-key"

    def test_multiple_sources_rejected(self):
        env = base_env(AGENT_REPOS="[]")
        with pytest.raises(RunnerConfigError, match="only one"):
            RunnerSettings.from_env(env)

    def test_no_source_allowed_at_parse_time(self):
        """No env source is legal: the Hub profile may supply config_source."""
        env = base_env()
        del env["AGENT_CONFIG_DIR"]
        settings = RunnerSettings.from_env(env)
        assert settings.config_dir is None
        assert settings.repos_file is None
        assert settings.agent_repos_json is None

    def test_dry_run_variants(self):
        assert RunnerSettings.from_env(base_env(DRY_RUN="1")).dry_run is True
        assert RunnerSettings.from_env(base_env(DRY_RUN="true")).dry_run is True
        assert RunnerSettings.from_env(base_env(DRY_RUN="")).dry_run is False

    def test_inline_repos_materialized(self, tmp_path):
        repos = [{"name": "defs", "url": "https://git.example.com/defs.git",
                  "clone_path": "/configs/defs"}]
        env = base_env()
        del env["AGENT_CONFIG_DIR"]
        env["AGENT_REPOS"] = json.dumps(repos)
        env["STATE_DIR"] = str(tmp_path)
        settings = RunnerSettings.from_env(env)
        path = Path(settings.effective_repos_file())
        assert path.exists()
        assert json.loads(path.read_text()) == repos

    def test_inline_repos_rejects_non_array(self, tmp_path):
        env = base_env()
        del env["AGENT_CONFIG_DIR"]
        env["AGENT_REPOS"] = '{"name": "x"}'
        env["STATE_DIR"] = str(tmp_path)
        settings = RunnerSettings.from_env(env)
        with pytest.raises(RunnerConfigError, match="JSON array"):
            settings.effective_repos_file()

    def test_inline_repos_rejects_invalid_json(self, tmp_path):
        env = base_env()
        del env["AGENT_CONFIG_DIR"]
        env["AGENT_REPOS"] = "not json at all"
        env["STATE_DIR"] = str(tmp_path)
        settings = RunnerSettings.from_env(env)
        with pytest.raises(RunnerConfigError, match="not valid JSON"):
            settings.effective_repos_file()

    def test_config_source_repos_file_built_under_state_dir(self, tmp_path):
        env = base_env()
        del env["AGENT_CONFIG_DIR"]
        env["STATE_DIR"] = str(tmp_path)
        settings = RunnerSettings.from_env(env)
        settings.config_source = "https://git.example.com/defs.git"
        path = Path(settings.config_source_repos_file())
        repos = json.loads(path.read_text())
        assert len(repos) == 1
        assert repos[0]["url"] == "https://git.example.com/defs.git"
        assert str(tmp_path) in repos[0]["clone_path"]

    def test_config_source_repos_file_none_without_source(self):
        env = base_env()
        del env["AGENT_CONFIG_DIR"]
        assert RunnerSettings.from_env(env).config_source_repos_file() is None

    def test_state_file_namespaced_per_agent(self):
        settings = RunnerSettings.from_env(base_env())
        assert settings.state_file.name == "simple-bot.state.json"


class TestDirectConfigLoader:
    def test_loads_config_and_prompt(self, definitions_dir):
        loader = DirectConfigLoader(str(definitions_dir))
        config = loader.load("simple-bot", "https://git.example.com/defs.git")
        assert config.name == "simple-bot"
        assert config.brain["provider"] == "anthropic"
        assert "Be friendly" in config.system_prompt
        assert config.config_source == "https://git.example.com/defs.git"

    def test_supports_flat_layout(self, tmp_path):
        agent_dir = tmp_path / "simple-bot"
        agent_dir.mkdir()
        (agent_dir / "config.yaml").write_text(yaml.safe_dump(AGENT_CONFIG))
        loader = DirectConfigLoader(str(tmp_path))
        assert loader.load("simple-bot").name == "simple-bot"

    def test_missing_agent_returns_none(self, definitions_dir):
        loader = DirectConfigLoader(str(definitions_dir))
        assert loader.load("nope") is None

    def test_missing_system_prompt_is_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "bare"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: bare\n")
        config = DirectConfigLoader(str(tmp_path)).load("bare")
        assert config.system_prompt is None


class TestAgentDefinitionLoader:
    def test_direct_mode(self, definitions_dir):
        settings = RunnerSettings.from_env(
            base_env(AGENT_CONFIG_DIR=str(definitions_dir))
        )
        config = AgentDefinitionLoader(settings).load("simple-bot")
        assert config.name == "simple-bot"

    def test_multi_repo_mode(self, tmp_path):
        # Point a repos.json at a local "remote" clone to exercise the git
        # loader path without the network.
        remote, _ = _local_git_remote(tmp_path)

        repos_file = tmp_path / "repos.json"
        repos_file.write_text(json.dumps([
            {"name": "defs", "url": "file://" + str(remote),
             "clone_path": str(tmp_path / "cloned")}
        ]))

        env = base_env(REPOS_FILE=str(repos_file), STATE_DIR=str(tmp_path / "state"))
        del env["AGENT_CONFIG_DIR"]
        settings = RunnerSettings.from_env(env)
        settings.git_timeout_seconds = 60
        config = AgentDefinitionLoader(settings).load("simple-bot")
        assert config.name == "simple-bot"
        assert "Be friendly" in config.system_prompt

    def test_missing_agent_raises(self, definitions_dir):
        settings = RunnerSettings.from_env(
            base_env(AGENT_CONFIG_DIR=str(definitions_dir))
        )
        with pytest.raises(RunnerConfigError, match="definition"):
            AgentDefinitionLoader(settings).load("nope")

    def test_no_source_raises_on_ensure_ready(self):
        env = base_env()
        del env["AGENT_CONFIG_DIR"]
        loader = AgentDefinitionLoader(RunnerSettings.from_env(env))
        with pytest.raises(RunnerConfigError, match="No agent definitions source"):
            loader.ensure_ready()

    def test_load_without_source_raises(self):
        env = base_env()
        del env["AGENT_CONFIG_DIR"]
        loader = AgentDefinitionLoader(RunnerSettings.from_env(env))
        with pytest.raises(RunnerConfigError, match="No agent definitions source"):
            loader.load("simple-bot")

    def test_profile_config_source_drives_git_loading(self, tmp_path):
        """No env source: the registered config_source (as the Hub profile
        would supply it) is used as a single-repo definitions source."""
        remote, git = _local_git_remote(tmp_path)
        env = base_env(STATE_DIR=str(tmp_path / "state"))
        del env["AGENT_CONFIG_DIR"]
        settings = RunnerSettings.from_env(env)
        settings.config_source = "file://" + str(remote)  # as GET /agents/me returns
        settings.git_timeout_seconds = 60

        loader = AgentDefinitionLoader(settings)
        loader.ensure_ready()  # must not raise once config_source is known
        config = loader.load("simple-bot")
        assert config.name == "simple-bot"
        assert "Be friendly" in config.system_prompt
        assert config.config_source == "file://" + str(remote)

    def test_ensure_ready_is_idempotent(self, definitions_dir):
        settings = RunnerSettings.from_env(
            base_env(AGENT_CONFIG_DIR=str(definitions_dir))
        )
        loader = AgentDefinitionLoader(settings)
        loader.ensure_ready()
        loader.ensure_ready()  # second call must not rebuild or raise
        assert loader.load("simple-bot").name == "simple-bot"


class TestBehaviorAccessors:
    def make_config(self, behavior=None, interests=None):
        class Cfg:
            pass

        cfg = Cfg()
        cfg.name = "simple-bot"
        cfg.display_name = "Simple Bot"
        cfg.description = "bot"
        cfg.behavior = behavior or {}
        cfg.interests = interests
        cfg.system_prompt = "You are {{name}}, {{description}}."
        return cfg

    def test_notification_flag_defaults(self):
        flags = get_notification_flags(self.make_config())
        assert flags["mention"] is True
        assert flags["reply"] is True
        assert flags["dm"] is False
        assert flags["thread_update"] is False
        assert flags["follow"] is False

    def test_notification_flag_overrides(self):
        behavior = {"notifications": {"respond_to_mentions": False,
                                      "respond_to_dms": True}}
        flags = get_notification_flags(self.make_config(behavior))
        assert flags["mention"] is False
        assert flags["dm"] is True

    def test_limits_defaults(self):
        limits = get_limits(self.make_config())
        assert limits == {
            "max_daily_posts": 5,
            "max_daily_comments": 50,
            "max_responses_per_thread": 3,
            "min_interval_seconds": 60,
        }

    def test_min_interval_comments_fallback(self):
        behavior = {"limits": {"min_interval_comments": 45}}
        assert get_limits(self.make_config(behavior))["min_interval_seconds"] == 45

    def test_discovery_defaults_disabled(self):
        settings = get_discovery_settings(self.make_config())
        assert settings["enabled"] is False
        assert settings["min_confidence"] == 0.7
        assert settings["proactive_interval_seconds"] == 3600

    def test_discovery_from_config(self):
        behavior = {"discovery": {"enabled": True, "min_confidence": 0.9,
                                  "proactive_interval": 600}}
        settings = get_discovery_settings(self.make_config(behavior))
        assert settings["enabled"] is True
        assert settings["min_confidence"] == 0.9
        assert settings["proactive_interval_seconds"] == 600

    def test_normalize_interests_canonical(self):
        interests = normalize_interests(self.make_config(
            interests={"topics": ["rust"], "communities": ["m/rust"],
                       "keywords": ["error"]}
        ))
        assert interests == {
            "topics": ["rust"],
            "communities": ["m/rust"],
            "keywords": ["error"],
        }

    def test_normalize_interests_adr009_fallbacks(self):
        behavior = {
            "notifications": {"watch_communities": ["m/debugging"],
                              "watch_keywords": ["bug"]},
            "discovery": {"interests": ["rust", "kubernetes"]},
        }
        interests = normalize_interests(self.make_config(behavior=behavior))
        assert interests["topics"] == ["rust", "kubernetes"]
        assert interests["communities"] == ["m/debugging"]
        assert interests["keywords"] == ["bug"]

    def test_render_system_prompt_substitutes_identity(self):
        prompt = render_system_prompt(self.make_config())
        assert "You are simple-bot, bot." in prompt

    def test_render_system_prompt_passthrough(self):
        cfg = self.make_config()
        cfg.system_prompt = "Static prompt."
        assert render_system_prompt(cfg) == "Static prompt."
