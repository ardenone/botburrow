"""Tests for reference-only CI registration result forwarding."""

import hashlib
import hmac
import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from ci_webhook_sender import (
    generate_signature,
    load_registration_results,
    parse_registration_output,
    reference_only_agents,
    send_webhook,
)


def reference(name="agent1"):
    return {
        "name": name,
        "api_key_ref": f"secret/ardenone-cluster/botburrow/agents/{name}",
        "config_source": "https://git.example/agents.git",
        "config_path": f"agents/{name}",
        "config_branch": "main",
    }


class TestGenerateSignature:
    def test_signature_is_verifiable(self):
        payload = b'{"test":"data"}'
        secret = "test-secret"
        signature = generate_signature(payload, secret)
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert signature == f"sha256={expected}"


class TestReferenceOnly:
    def test_removes_unrelated_fields(self):
        agent = reference()
        agent["valid"] = True
        assert reference_only_agents([agent]) == [reference()]

    def test_rejects_plaintext_key(self):
        with pytest.raises(ValueError, match="plaintext API-key"):
            reference_only_agents([{**reference(), "api_key": "never-forward"}])

    def test_requires_openbao_reference(self):
        with pytest.raises(ValueError, match="api_key_ref"):
            reference_only_agents([{"name": "agent1"}])


class TestSendWebhook:
    @patch("ci_webhook_sender.requests.post")
    def test_sends_reference_only_payload(self, mock_post):
        response = Mock()
        response.json.return_value = {"success": True}
        response.raise_for_status = Mock()
        mock_post.return_value = response

        send_webhook(
            webhook_url="https://botburrow.example/webhook",
            webhook_secret="test-secret",
            repository="https://git.example/agents.git",
            branch="main",
            commit_sha="abc123",
            agents=[reference()],
        )

        payload = json.loads(mock_post.call_args.kwargs["data"])
        assert payload["agents"] == [reference()]
        assert '"api_key":' not in json.dumps(payload)

    @patch("ci_webhook_sender.requests.post")
    def test_rejects_plaintext_before_network_call(self, mock_post):
        with pytest.raises(ValueError, match="plaintext API-key"):
            send_webhook(
                webhook_url="https://botburrow.example/webhook",
                webhook_secret="test-secret",
                repository="https://git.example/agents.git",
                branch="main",
                commit_sha="abc123",
                agents=[{**reference(), "api_key": "never-forward"}],
            )
        mock_post.assert_not_called()

    @patch("ci_webhook_sender.requests.post")
    def test_network_errors_are_raised_without_response_body_logging(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("connection refused")
        with pytest.raises(requests.exceptions.RequestException):
            send_webhook(
                webhook_url="https://botburrow.example/webhook",
                webhook_secret="test-secret",
                repository="https://git.example/agents.git",
                branch="main",
                commit_sha="abc123",
                agents=[reference()],
            )


class TestResultLoading:
    def test_loads_reference_list(self, tmp_path):
        path = tmp_path / "results.json"
        path.write_text(json.dumps({"agents": [reference()]}))
        assert load_registration_results(path) == [reference()]

    def test_rejects_legacy_secret_file(self, tmp_path):
        path = tmp_path / "results.json"
        path.write_text(json.dumps({"agents": [{"name": "agent1", "api_key": "never-forward"}]}))
        with pytest.raises(ValueError, match="plaintext API-key"):
            load_registration_results(path)

    def test_parses_openbao_reference_from_log(self):
        output = """
Agent 'test-agent' registered successfully
  API key delivered to: secret/ardenone-cluster/botburrow/agents/test-agent (field: api-key, version: 1)
"""
        assert parse_registration_output(output) == [{
            "name": "test-agent",
            "api_key_ref": "secret/ardenone-cluster/botburrow/agents/test-agent",
        }]

    def test_rejects_legacy_key_log(self):
        with pytest.raises(ValueError, match="Plaintext API-key"):
            parse_registration_output("Agent 'agent1'\n  API Key: never-forward")
