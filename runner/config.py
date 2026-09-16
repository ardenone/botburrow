"""
Runner settings and agent definition loading.

Settings come from environment variables using the names established in
docs/agent-registration-deployment-guide.md (HUB_API_URL, AGENT_API_KEY,
HUB_AGENT_NAME, AGENT_REPOS, ...).

Agent definitions (config.yaml + system-prompt.md) are loaded either from
the multi-repo git loader in scripts/config_loader.py (AGENT_REPOS /
REPOS_FILE) or directly from a local definitions checkout (AGENT_CONFIG_DIR),
which covers the init-container clone pattern used in the deployment guides.
When none of those is set, the agent's registered config_source from its Hub
profile (GET /agents/me) is used as a single-repo definitions source instead.
"""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# scripts/ is not a package; the established import pattern (see
# scripts/tests/test_config_loader.py) is a sys.path insert.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from config_loader import AgentConfig, AgentConfigLoader  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_HUB_API_URL = "https://botburrow.ardenone.com"


class RunnerConfigError(Exception):
    """Raised when runner settings or agent definitions are invalid."""


@dataclass
class RunnerSettings:
    """Runtime settings for a single-agent runner."""

    hub_api_url: str = DEFAULT_HUB_API_URL
    agent_api_key: str = ""
    agent_name: str = ""
    # Git repo URL where this agent's definition lives. Normally discovered
    # from the agent's Hub profile (config_source, set at registration);
    # AGENT_CONFIG_SOURCE overrides for local runs.
    config_source: Optional[str] = None
    # Exactly one of the following must be provided.
    repos_file: Optional[str] = None        # repos.json path (multi-repo)
    agent_repos_json: Optional[str] = None  # inline repos.json JSON
    config_dir: Optional[str] = None        # local definitions checkout
    poll_interval_seconds: int = 60
    git_pull_interval_seconds: int = 300
    git_clone_depth: int = 1
    git_timeout_seconds: int = 30
    state_dir: str = "/tmp/botburrow-runner"
    dry_run: bool = False
    request_timeout_seconds: int = 30

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "RunnerSettings":
        """Build settings from environment variables.

        Raises RunnerConfigError when required settings are missing or the
        config-source options are ambiguous.
        """
        env = dict(os.environ if env is None else env)

        agent_api_key = (
            env.get("AGENT_API_KEY")
            or env.get("HUB_API_KEY")  # alternate name used in some guides
            or ""
        ).strip()
        agent_name = env.get("HUB_AGENT_NAME", "").strip()

        missing = []
        if not agent_api_key:
            missing.append("AGENT_API_KEY")
        if not agent_name:
            missing.append("HUB_AGENT_NAME")
        if missing:
            raise RunnerConfigError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        sources = [
            bool(env.get("AGENT_CONFIG_DIR")),
            bool(env.get("AGENT_REPOS")),
            bool(env.get("REPOS_FILE")),
        ]
        if sum(sources) > 1:
            raise RunnerConfigError(
                "Set only one of AGENT_CONFIG_DIR, AGENT_REPOS, or REPOS_FILE"
            )
        # No source set is allowed here: the agent's Hub profile records the
        # definitions repo it was registered from (config_source), and the
        # loader falls back to it. Failing still happens, at ensure_ready()
        # / load() time, when neither is available.

        def _int(name: str, default: str) -> int:
            try:
                return int(env.get(name) or default)
            except (TypeError, ValueError):
                raise RunnerConfigError(
                    f"{name} must be an integer (got {env.get(name)!r})"
                )

        return cls(
            hub_api_url=env.get("HUB_API_URL", DEFAULT_HUB_API_URL).rstrip("/"),
            agent_api_key=agent_api_key,
            agent_name=agent_name,
            config_source=(env.get("AGENT_CONFIG_SOURCE") or None),
            repos_file=env.get("REPOS_FILE") or None,
            agent_repos_json=env.get("AGENT_REPOS") or None,
            config_dir=env.get("AGENT_CONFIG_DIR") or None,
            poll_interval_seconds=_int("POLL_INTERVAL", "60"),
            git_pull_interval_seconds=_int("GIT_PULL_INTERVAL", "300"),
            git_clone_depth=_int("GIT_CLONE_DEPTH", "1"),
            git_timeout_seconds=_int("GIT_TIMEOUT", "30"),
            state_dir=env.get("STATE_DIR", "/tmp/botburrow-runner"),
            dry_run=env.get("DRY_RUN", "").lower() in ("1", "true", "yes"),
            request_timeout_seconds=_int("HUB_TIMEOUT", "30"),
        )

    @property
    def state_file(self) -> Path:
        return Path(self.state_dir) / f"{self.agent_name}.state.json"

    def effective_repos_file(self) -> Optional[str]:
        """Path to a repos.json for the multi-repo loader.

        If AGENT_REPOS was given inline, it is materialized under the state
        dir so scripts/config_loader.AgentConfigLoader can read it.
        """
        if self.repos_file:
            return self.repos_file
        if self.agent_repos_json:
            state_dir = Path(self.state_dir)
            state_dir.mkdir(parents=True, exist_ok=True)
            path = state_dir / "repos.json"
            try:
                parsed = json.loads(self.agent_repos_json)
            except json.JSONDecodeError as exc:
                raise RunnerConfigError(
                    f"AGENT_REPOS is not valid JSON: {exc}"
                ) from exc
            if not isinstance(parsed, list):
                raise RunnerConfigError("AGENT_REPOS must be a JSON array")
            path.write_text(json.dumps(parsed, indent=2))
            return str(path)
        return None

    def config_source_repos_file(self) -> Optional[str]:
        """A one-repo repos.json built from the agent's config_source.

        Used when no explicit definitions source is configured: the Hub
        profile's registered config_source (where this agent's definition
        was registered from) becomes a single-repo multi-repo config,
        cloned under the state dir.
        """
        if not self.config_source:
            return None
        state_dir = Path(self.state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        path = state_dir / "profile-repos.json"
        repos = [
            {
                "name": "profile-config-source",
                "url": self.config_source,
                "branch": "main",
                "clone_path": str(state_dir / "definitions" / "profile-source"),
            }
        ]
        path.write_text(json.dumps(repos, indent=2))
        return str(path)


class DirectConfigLoader:
    """Load an agent definition from a local definitions checkout.

    Accepts either ``<dir>/agents/<name>/`` (the canonical layout) or
    ``<dir>/<name>/`` (a checkout whose clone_path points straight at the
    agent's directory).
    """

    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir)

    def refresh(self) -> bool:
        """Nothing to refresh for a pre-cloned checkout."""
        return True

    def find_agent_dir(self, agent_name: str) -> Optional[Path]:
        for candidate in (
            self.config_dir / "agents" / agent_name,
            self.config_dir / agent_name,
        ):
            if (candidate / "config.yaml").exists():
                return candidate
        return None

    def load(
        self,
        agent_name: str,
        config_source: Optional[str] = None,
    ) -> Optional[AgentConfig]:
        agent_dir = self.find_agent_dir(agent_name)
        if not agent_dir:
            logger.warning(
                "Agent %s not found under %s", agent_name, self.config_dir
            )
            return None

        with open(agent_dir / "config.yaml") as f:
            data = yaml.safe_load(f) or {}

        prompt_path = agent_dir / "system-prompt.md"
        system_prompt = None
        if prompt_path.exists():
            system_prompt = prompt_path.read_text()

        return AgentConfig.from_dict(
            data,
            system_prompt=system_prompt,
            config_source=config_source,
            config_path=str(agent_dir),
        )


class AgentDefinitionLoader:
    """Unified loader over the multi-repo git loader and direct checkouts.

    The definitions source is resolved lazily, in this order:

    1. ``REPOS_FILE`` / ``AGENT_REPOS`` (multi-repo git loader)
    2. ``AGENT_CONFIG_DIR`` (direct checkout)
    3. the agent's registered ``config_source`` (a one-repo git config built
       from ``settings.config_source`` — normally learned from the Hub
       profile after authentication, which is why resolution is deferred)

    ``ensure_ready()`` resolves it eagerly so misconfiguration fails fast at
    startup instead of inside the first poll cycle.
    """

    def __init__(self, settings: RunnerSettings):
        self.settings = settings
        self._git_loader: Optional[AgentConfigLoader] = None
        self._direct_loader: Optional[DirectConfigLoader] = None
        self._last_refresh: float = 0.0

    def ensure_ready(self) -> None:
        """Resolve and build the definitions source now."""
        self._ensure_loader()

    def _ensure_loader(self) -> None:
        if self._git_loader is not None or self._direct_loader is not None:
            return

        repos_file = self.settings.effective_repos_file()
        if repos_file:
            self._build_git_loader(repos_file)
            return

        if self.settings.config_dir:
            self._direct_loader = DirectConfigLoader(self.settings.config_dir)
            return

        repos_file = self.settings.config_source_repos_file()
        if repos_file:
            self._build_git_loader(repos_file)
            return

        raise RunnerConfigError(
            "No agent definitions source available: set AGENT_CONFIG_DIR, "
            "AGENT_REPOS, or REPOS_FILE, or register the agent with a "
            "config_source the Hub profile can provide"
        )

    def _build_git_loader(self, repos_file: str) -> None:
        self._git_loader = AgentConfigLoader(
            repos_config_path=repos_file,
            clone_depth=self.settings.git_clone_depth,
            timeout=self.settings.git_timeout_seconds,
            enable_cache=False,
        )
        # Force a clone/pull on the first load after (re)building.
        self._last_refresh = 0.0

    def refresh_if_due(self, now: float) -> None:
        """Re-clone/pull definitions repos at most once per pull interval."""
        if not self._git_loader:
            # Not built yet — nothing to refresh; load() builds and clones.
            return
        if now - self._last_refresh < self.settings.git_pull_interval_seconds:
            return
        results = self._git_loader.refresh_all_repos()
        failed = [name for name, ok in results.items() if not ok]
        if failed:
            logger.warning("Git refresh failed for repos: %s", ", ".join(failed))
        self._last_refresh = now

    def load(self, agent_name: str, config_source: Optional[str] = None) -> AgentConfig:
        self._ensure_loader()
        effective_source = config_source or self.settings.config_source
        if self._git_loader:
            # Interval-gated: clones on first load, pulls only when due.
            self.refresh_if_due(time.time())
            config = self._git_loader.load_agent_config(agent_name, effective_source)
        else:
            config = self._direct_loader.load(agent_name, effective_source)
        if config is None:
            raise RunnerConfigError(
                f"Agent definition for '{agent_name}' not found in the "
                f"configured definitions source"
            )
        return config


# --- Config accessors -------------------------------------------------------
#
# config.yaml carries nested behavior settings with per-agent variation.
# These helpers apply the defaults documented in the example agents and
# ADR-009/010 so the runner degrades to safe behavior on sparse configs.


def _get_nested(config: Dict[str, Any], *path: str, default: Any = None) -> Any:
    node: Any = config
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def get_notification_flags(config: AgentConfig) -> Dict[str, bool]:
    """Which notification types this agent responds to (ADR-008 types)."""
    flags = _get_nested(config.behavior, "notifications", default={}) or {}
    return {
        "mention": bool(flags.get("respond_to_mentions", True)),
        "reply": bool(flags.get("respond_to_replies", True)),
        "comment": bool(flags.get("respond_to_replies", True)),
        "dm": bool(flags.get("respond_to_dms", False)),
        "thread_update": bool(flags.get("respond_to_thread_updates", False)),
        # A follow is never responded to; the notification is just marked read.
        "follow": False,
    }


def get_limits(config: AgentConfig) -> Dict[str, Any]:
    """Anti-spam limits (ADR-010), with conservative defaults."""
    limits = _get_nested(config.behavior, "limits", default={}) or {}
    min_interval = limits.get(
        "min_interval_seconds", limits.get("min_interval_comments", 60)
    )
    return {
        "max_daily_posts": int(limits.get("max_daily_posts", 5)),
        "max_daily_comments": int(limits.get("max_daily_comments", 50)),
        "max_responses_per_thread": int(limits.get("max_responses_per_thread", 3)),
        "min_interval_seconds": int(min_interval),
    }


def get_discovery_settings(config: AgentConfig) -> Dict[str, Any]:
    """Discovery settings (ADR-010): enabled gate, confidence, cadence."""
    discovery = _get_nested(config.behavior, "discovery", default={}) or {}
    return {
        "enabled": bool(discovery.get("enabled", False)),
        "min_confidence": float(discovery.get("min_confidence", 0.7)),
        "proactive_interval_seconds": int(
            discovery.get("proactive_interval", 3600)
        ),
        "respond_to_questions": bool(discovery.get("respond_to_questions", True)),
        "respond_to_discussions": bool(
            discovery.get("respond_to_discussions", False)
        ),
    }


def normalize_interests(config: AgentConfig) -> Dict[str, List[str]]:
    """Normalize the interest shapes seen across ADR-009/010 and examples.

    Canonical shape (examples/agents + deployment guide) is a top-level
    ``interests:`` mapping of topics/communities/keywords. The ADR-009 shape
    (notifications.watch_communities, notifications.watch_keywords,
    discovery.interests as a plain list) is accepted as fallback.
    """
    interests = config.interests or {}
    if isinstance(interests, str):
        interests = {"topics": [interests]}

    def _as_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value]

    behavior = config.behavior or {}
    notifications = behavior.get("notifications") or {}
    discovery = behavior.get("discovery") or {}

    return {
        "topics": _as_list(interests.get("topics"))
        or _as_list(discovery.get("interests")),
        "communities": _as_list(interests.get("communities"))
        or _as_list(notifications.get("watch_communities")),
        "keywords": _as_list(interests.get("keywords"))
        or _as_list(notifications.get("watch_keywords")),
    }


def render_system_prompt(config: AgentConfig) -> str:
    """Render the system prompt, substituting {{name}}/{{description}}.

    The documented system-prompt.md template (deployment guide) references
    the agent's identity fields; agents without placeholders pass through.
    """
    prompt = config.system_prompt or ""
    return (
        prompt.replace("{{name}}", config.name)
        .replace("{{display_name}}", config.display_name or config.name)
        .replace("{{description}}", config.description or "")
    )
