"""
Tests for brain providers.

Covers config resolution (API keys from env only), request shapes for the
Anthropic and OpenAI REST providers, response parsing, error mapping, and
the scriptable mock brain.
"""

import pytest

from runner.brain import (
    AnthropicBrain,
    BrainConfig,
    BrainError,
    MockBrain,
    OpenAIBrain,
    create_brain,
)


class StubResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


class StubSession:
    def __init__(self, response=None):
        self.response = response or StubResponse(200, {})
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


ENV = {"BRAIN_API_KEY": "sk-test"}


class TestBrainConfig:
    def test_key_from_brain_api_key(self):
        cfg = BrainConfig.from_agent_config({"provider": "anthropic"}, env=ENV)
        assert cfg.api_key == "sk-test"

    def test_key_from_provider_specific_var(self):
        cfg = BrainConfig.from_agent_config(
            {"provider": "openai"}, env={"OPENAI_API_KEY": "oai"}
        )
        assert cfg.api_key == "oai"

    def test_key_from_named_env_var(self):
        cfg = BrainConfig.from_agent_config(
            {"provider": "anthropic", "api_key_env": "MY_LLM_KEY"},
            env={"MY_LLM_KEY": "named"},
        )
        assert cfg.api_key == "named"

    def test_missing_key_raises_for_real_providers(self):
        with pytest.raises(BrainError, match="anthropic"):
            BrainConfig.from_agent_config({"provider": "anthropic"}, env={})
        with pytest.raises(BrainError, match="openai"):
            BrainConfig.from_agent_config({"provider": "openai"}, env={})

    def test_mock_needs_no_key(self):
        cfg = BrainConfig.from_agent_config({"provider": "mock"}, env={})
        assert cfg.api_key == ""

    def test_defaults(self):
        cfg = BrainConfig.from_agent_config({}, env={})
        assert cfg.provider == "mock"
        assert cfg.max_tokens == 1024
        assert cfg.temperature == 0.7
        assert cfg.top_p is None


class TestAnthropicBrain:
    def make(self, session):
        config = BrainConfig.from_agent_config(
            {"provider": "anthropic", "model": "claude-haiku-3-20250515",
             "max_tokens": 512, "temperature": 0.4},
            env=ENV,
        )
        return AnthropicBrain(config, session=session)

    def test_request_shape_and_parsing(self):
        session = StubSession(StubResponse(200, {
            "content": [{"type": "text", "text": "Hello"},
                        {"type": "text", "text": " world"}],
        }))
        brain = self.make(session)
        reply = brain.generate(
            "be nice",
            [{"role": "user", "content": "hi"}],
        )
        assert reply == "Hello world"

        call = session.calls[0]
        assert call["url"] == "https://api.anthropic.com/v1/messages"
        assert call["headers"]["x-api-key"] == "sk-test"
        assert call["headers"]["anthropic-version"] == "2023-06-01"
        assert call["json"]["system"] == "be nice"
        assert call["json"]["model"] == "claude-haiku-3-20250515"
        assert call["json"]["max_tokens"] == 512
        assert call["json"]["temperature"] == 0.4
        assert call["json"]["messages"] == [{"role": "user", "content": "hi"}]

    def test_error_maps_to_brain_error(self):
        brain = self.make(StubSession(StubResponse(429, {}, text="rate limited")))
        with pytest.raises(BrainError, match="429"):
            brain.generate("s", [{"role": "user", "content": "hi"}])

    def test_missing_model_raises(self):
        config = BrainConfig(provider="anthropic", api_key="sk")
        with pytest.raises(BrainError, match="model"):
            AnthropicBrain(config).generate("s", [])

    def test_bad_response_shape_raises(self):
        brain = self.make(StubSession(StubResponse(200, {"weird": True})))
        with pytest.raises(BrainError, match="shape"):
            brain.generate("s", [{"role": "user", "content": "hi"}])


class TestOpenAIBrain:
    def make(self, session):
        config = BrainConfig.from_agent_config(
            {"provider": "openai", "model": "gpt-4o-mini", "top_p": 0.9},
            env=ENV,
        )
        return OpenAIBrain(config, session=session)

    def test_request_shape_and_parsing(self):
        session = StubSession(StubResponse(200, {
            "choices": [{"message": {"role": "assistant", "content": "  hi there "}}],
        }))
        brain = self.make(session)
        reply = brain.generate("sys", [{"role": "user", "content": "q"}])
        assert reply == "hi there"

        call = session.calls[0]
        assert call["url"] == "https://api.openai.com/v1/chat/completions"
        assert call["headers"]["Authorization"] == "Bearer sk-test"
        messages = call["json"]["messages"]
        assert messages[0] == {"role": "system", "content": "sys"}
        assert messages[1]["role"] == "user"
        assert call["json"]["top_p"] == 0.9

    def test_error_maps_to_brain_error(self):
        brain = self.make(StubSession(StubResponse(500, {}, text="oops")))
        with pytest.raises(BrainError, match="500"):
            brain.generate("s", [{"role": "user", "content": "hi"}])


class TestMockBrain:
    def test_scripted_responses_in_order(self):
        brain = MockBrain(responses=["first", "SKIP"])
        messages = [{"role": "user", "content": "x"}]
        assert brain.generate("s", messages) == "first"
        assert brain.generate("s", messages) == "SKIP"

    def test_default_after_script_exhausted(self):
        brain = MockBrain(responses=["only"])
        messages = [{"role": "user", "content": "x"}]
        brain.generate("s", messages)
        assert "mock response" in brain.generate("s", messages)


class TestFactory:
    def test_creates_mock(self):
        brain = create_brain({"provider": "mock"}, env={})
        assert isinstance(brain, MockBrain)

    def test_unknown_provider_raises(self):
        with pytest.raises(BrainError, match="Unsupported"):
            create_brain({"provider": "hal-9000"}, env={})
