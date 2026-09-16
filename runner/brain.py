"""
Brain providers: generate agent responses via the configured LLM.

The provider is selected by config.yaml's ``brain:`` block:

    brain:
      provider: "anthropic"          # anthropic | openai | mock
      model: "claude-haiku-3-20250515"
      max_tokens: 1024
      temperature: 0.5

Provider API keys come from the environment, never from config.yaml —
the definitions repo is git-tracked and config values like
``secret:github-token`` are references, not credentials. Resolution order:

1. ``brain.api_key_env`` naming the env var that holds the key
2. ``BRAIN_API_KEY``
3. ``ANTHROPIC_API_KEY`` (anthropic) / ``OPENAI_API_KEY`` (openai)

Both HTTP providers are plain REST calls (requests) — no SDK dependency.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
ANTHROPIC_VERSION = "2023-06-01"

Message = Dict[str, str]


class BrainError(Exception):
    """Raised when the brain is misconfigured or a generation fails."""


@dataclass
class BrainConfig:
    provider: str = "mock"
    model: str = ""
    max_tokens: int = 1024
    temperature: float = 0.7
    top_p: Optional[float] = None
    api_key: str = ""
    api_base: Optional[str] = None

    @classmethod
    def from_agent_config(
        cls, brain_cfg: Dict[str, Any], env: Optional[Dict[str, str]] = None
    ) -> "BrainConfig":
        env = dict(os.environ if env is None else env)

        provider = str(brain_cfg.get("provider", "mock")).lower()
        model = str(brain_cfg.get("model", ""))

        api_key = _resolve_api_key(provider, brain_cfg, env)
        if provider in ("anthropic", "openai") and not api_key:
            raise BrainError(
                f"No API key for brain provider '{provider}'. Set "
                f"BRAIN_API_KEY or {provider.upper()}_API_KEY (or point "
                f"brain.api_key_env at the env var holding it)."
            )

        return cls(
            provider=provider,
            model=model,
            max_tokens=int(brain_cfg.get("max_tokens", 1024)),
            temperature=float(brain_cfg.get("temperature", 0.7)),
            top_p=(
                float(brain_cfg["top_p"]) if brain_cfg.get("top_p") is not None else None
            ),
            api_key=api_key,
            api_base=brain_cfg.get("api_base") or None,
        )


def _resolve_api_key(
    provider: str, brain_cfg: Dict[str, Any], env: Dict[str, str]
) -> str:
    """Find the provider API key in the environment. Never logs the value."""
    candidates: List[str] = []
    named_env = brain_cfg.get("api_key_env")
    if named_env:
        candidates.append(str(named_env))
    candidates.append("BRAIN_API_KEY")
    candidates.append(f"{provider.upper()}_API_KEY")

    for name in candidates:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return ""


def create_brain(brain_cfg: Dict[str, Any], env=None) -> "BaseBrain":
    """Factory: build the brain named by the agent's config."""
    config = BrainConfig.from_agent_config(brain_cfg or {}, env=env)
    if config.provider == "anthropic":
        return AnthropicBrain(config)
    if config.provider == "openai":
        return OpenAIBrain(config)
    if config.provider == "mock":
        return MockBrain(config)
    raise BrainError(f"Unsupported brain provider: {config.provider}")


class BaseBrain:
    """Interface: generate one completion for a message list."""

    def __init__(self, config: BrainConfig):
        self.config = config

    def generate(
        self,
        system_prompt: str,
        messages: List[Message],
    ) -> str:
        raise NotImplementedError


class AnthropicBrain(BaseBrain):
    """Anthropic Messages API (https://api.anthropic.com/v1/messages)."""

    def __init__(
        self,
        config: BrainConfig,
        session: Optional[requests.Session] = None,
    ):
        super().__init__(config)
        self.session = session or requests.Session()

    def generate(self, system_prompt: str, messages: List[Message]) -> str:
        if not self.config.model:
            raise BrainError("brain.model is required for the anthropic provider")

        base = (self.config.api_base or DEFAULT_ANTHROPIC_BASE).rstrip("/")
        body: Dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "system": system_prompt,
            "messages": [
                {"role": m["role"], "content": m["content"]} for m in messages
            ],
        }
        if self.config.top_p is not None:
            body["top_p"] = self.config.top_p

        try:
            response = self.session.post(
                f"{base}/v1/messages",
                headers={
                    "x-api-key": self.config.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=body,
                timeout=120,
            )
        except requests.RequestException as exc:
            raise BrainError(f"Anthropic request failed: {exc}") from exc

        if response.status_code != 200:
            raise BrainError(
                f"Anthropic returned HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        data = response.json()
        try:
            return "".join(
                block["text"] for block in data["content"] if block.get("type") == "text"
            ).strip()
        except (KeyError, TypeError) as exc:
            raise BrainError(f"Unexpected Anthropic response shape: {data}") from exc


class OpenAIBrain(BaseBrain):
    """OpenAI Chat Completions API (also works for compatible gateways)."""

    def __init__(
        self,
        config: BrainConfig,
        session: Optional[requests.Session] = None,
    ):
        super().__init__(config)
        self.session = session or requests.Session()

    def generate(self, system_prompt: str, messages: List[Message]) -> str:
        if not self.config.model:
            raise BrainError("brain.model is required for the openai provider")

        base = (self.config.api_base or DEFAULT_OPENAI_BASE).rstrip("/")
        chat_messages = [{"role": "system", "content": system_prompt}] + [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        body: Dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "messages": chat_messages,
        }
        if self.config.top_p is not None:
            body["top_p"] = self.config.top_p

        try:
            response = self.session.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "content-type": "application/json",
                },
                json=body,
                timeout=120,
            )
        except requests.RequestException as exc:
            raise BrainError(f"OpenAI request failed: {exc}") from exc

        if response.status_code != 200:
            raise BrainError(
                f"OpenAI returned HTTP {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise BrainError(f"Unexpected OpenAI response shape: {data}") from exc


class MockBrain(BaseBrain):
    """Scriptable no-op brain for tests and --dry-run bring-up.

    Returns scripted responses in order; the last one repeats once scripted
    responses are exhausted. An empty script yields a fixed placeholder.
    """

    def __init__(
        self,
        config: Optional[BrainConfig] = None,
        responses: Optional[List[str]] = None,
    ):
        super().__init__(config or BrainConfig(provider="mock"))
        self.responses = list(responses or [])
        # Every (system_prompt, messages) pair it was asked to generate for —
        # used by tests to assert on prompt construction.
        self.prompts: List[tuple] = []

    def generate(self, system_prompt: str, messages: List[Message]) -> str:
        self.prompts.append((system_prompt, messages))
        if self.responses:
            return self.responses.pop(0)
        return "This is a mock response from the botburrow runner."
