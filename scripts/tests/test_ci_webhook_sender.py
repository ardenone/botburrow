"""
Tests for CI/CD webhook sender script.

Tests cover:
- Webhook signature generation
- Webhook sending functionality
- Registration results loading
- Output parsing
- Error handling
"""

import json
import hmac
import hashlib
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

# Import the modules to test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ci_webhook_sender import (
    generate_signature,
    send_webhook,
    load_registration_results,
    parse_registration_output,
)


class TestGenerateSignature:
    """Test HMAC signature generation."""

    def test_generate_signature_basic(self):
        """Test basic signature generation."""
        payload = b'{"test": "data"}'
        secret = "test-secret"

        signature = generate_signature(payload, secret)

        assert signature.startswith("sha256=")
        # Verify it's a valid hex string after the prefix
        hex_part = signature.split("=")[1]
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_generate_signature_consistent(self):
        """Test signature is consistent for same input."""
        payload = b'{"test": "data"}'
        secret = "test-secret"

        sig1 = generate_signature(payload, secret)
        sig2 = generate_signature(payload, secret)

        assert sig1 == sig2

    def test_generate_signature_different_inputs(self):
        """Test different inputs produce different signatures."""
        secret = "test-secret"

        sig1 = generate_signature(b'{"test": "data1"}', secret)
        sig2 = generate_signature(b'{"test": "data2"}', secret)

        assert sig1 != sig2

    def test_generate_signature_different_secrets(self):
        """Test different secrets produce different signatures."""
        payload = b'{"test": "data"}'

        sig1 = generate_signature(payload, "secret1")
        sig2 = generate_signature(payload, "secret2")

        assert sig1 != sig2

    def test_generate_signature_verifiable(self):
        """Test signature can be verified independently."""
        payload = b'{"test": "data"}'
        secret = "test-secret"

        signature = generate_signature(payload, secret)

        # Verify using standard HMAC
        expected_sig = hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        expected = f"sha256={expected_sig}"

        assert signature == expected


class TestSendWebhook:
    """Test webhook sending functionality."""

    @patch('ci_webhook_sender.requests.post')
    def test_send_webhook_success(self, mock_post):
        """Test successful webhook sending."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "success": True,
            "message": "Agents registered successfully",
            "repository": "https://github.com/test/repo.git",
            "commit_sha": "abc123",
            "secrets_created": [
                {"agent_name": "agent1", "secret_name": "agent-agent1", "success": True}
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        agents = [
            {
                "name": "agent1",
                "api_key": "botburrow_agent_abc123",
                "config_source": "https://github.com/test/repo.git",
                "config_path": "agents/agent1",
                "config_branch": "main",
            }
        ]

        result = send_webhook(
            webhook_url="https://botburrow.example.com/api/v1/webhooks/agent-registration",
            webhook_secret="test-secret",
            repository="https://github.com/test/repo.git",
            branch="main",
            commit_sha="abc123",
            agents=agents,
        )

        assert result["success"] is True
        assert mock_post.called

        # Verify request headers
        call_kwargs = mock_post.call_args[1]
        assert "X-Webhook-Signature" in call_kwargs["headers"]
        assert call_kwargs["headers"]["Content-Type"] == "application/json"

    @patch('ci_webhook_sender.requests.post')
    def test_send_webhook_with_run_info(self, mock_post):
        """Test webhook sending with CI run information."""
        mock_response = Mock()
        mock_response.json.return_value = {"success": True}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        agents = [{"name": "agent1", "api_key": "key1"}]

        send_webhook(
            webhook_url="https://botburrow.example.com/api/v1/webhooks/agent-registration",
            webhook_secret="test-secret",
            repository="https://github.com/test/repo.git",
            branch="main",
            commit_sha="abc123",
            agents=agents,
            run_id="12345",
            run_url="https://github.com/test/repo/actions/runs/12345",
        )

        # Verify payload includes run info
        call_kwargs = mock_post.call_args[1]
        payload = json.loads(call_kwargs["data"])
        assert payload["run_id"] == "12345"
        assert payload["run_url"] == "https://github.com/test/repo/actions/runs/12345"

    @patch('ci_webhook_sender.requests.post')
    def test_send_webhook_connection_error(self, mock_post):
        """Test webhook sending with connection error."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        agents = [{"name": "agent1", "api_key": "key1"}]

        with pytest.raises(requests.exceptions.RequestException):
            send_webhook(
                webhook_url="https://botburrow.example.com/api/v1/webhooks/agent-registration",
                webhook_secret="test-secret",
                repository="https://github.com/test/repo.git",
                branch="main",
                commit_sha="abc123",
                agents=agents,
            )

    @patch('ci_webhook_sender.requests.post')
    def test_send_webhook_http_error(self, mock_post):
        """Test webhook sending with HTTP error response."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
        mock_post.return_value = mock_response

        agents = [{"name": "agent1", "api_key": "key1"}]

        with pytest.raises(requests.exceptions.HTTPError):
            send_webhook(
                webhook_url="https://botburrow.example.com/api/v1/webhooks/agent-registration",
                webhook_secret="test-secret",
                repository="https://github.com/test/repo.git",
                branch="main",
                commit_sha="abc123",
                agents=agents,
            )

    @patch('ci_webhook_sender.requests.post')
    def test_send_webhook_timeout(self, mock_post):
        """Test webhook sending with timeout."""
        mock_post.side_effect = requests.exceptions.Timeout("Request timeout")

        agents = [{"name": "agent1", "api_key": "key1"}]

        with pytest.raises(requests.exceptions.Timeout):
            send_webhook(
                webhook_url="https://botburrow.example.com/api/v1/webhooks/agent-registration",
                webhook_secret="test-secret",
                repository="https://github.com/test/repo.git",
                branch="main",
                commit_sha="abc123",
                agents=agents,
                timeout=5,
            )

    @patch('ci_webhook_sender.requests.post')
    def test_send_webhook_signature_format(self, mock_post):
        """Test webhook signature is sent in correct format."""
        mock_response = Mock()
        mock_response.json.return_value = {"success": True}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        agents = [{"name": "agent1", "api_key": "key1"}]

        send_webhook(
            webhook_url="https://botburrow.example.com/api/v1/webhooks/agent-registration",
            webhook_secret="test-secret",
            repository="https://github.com/test/repo.git",
            branch="main",
            commit_sha="abc123",
            agents=agents,
        )

        call_kwargs = mock_post.call_args[1]
        signature = call_kwargs["headers"]["X-Webhook-Signature"]
        assert signature.startswith("sha256=")

        # Verify signature is correct
        payload = call_kwargs["data"]
        expected_sig = hmac.new(
            b"test-secret",
            payload,
            hashlib.sha256,
        ).hexdigest()
        assert signature == f"sha256={expected_sig}"


class TestLoadRegistrationResults:
    """Test loading registration results from files."""

    def test_load_from_list_format(self, tmp_path):
        """Test loading from list format JSON."""
        results_file = tmp_path / "results.json"
        results_file.write_text(json.dumps([
            {"name": "agent1", "api_key": "key1"},
            {"name": "agent2", "api_key": "key2"},
        ]))

        agents = load_registration_results(results_file)
        assert len(agents) == 2
        assert agents[0]["name"] == "agent1"

    def test_load_from_dict_with_agents_key(self, tmp_path):
        """Test loading from dict format with 'agents' key."""
        results_file = tmp_path / "results.json"
        results_file.write_text(json.dumps({
            "repository": "https://github.com/test/repo.git",
            "branch": "main",
            "agents": [
                {"name": "agent1", "api_key": "key1"},
            ]
        }))

        agents = load_registration_results(results_file)
        assert len(agents) == 1
        assert agents[0]["name"] == "agent1"

    def test_load_from_dict_with_results_key(self, tmp_path):
        """Test loading from dict format with 'results' key."""
        results_file = tmp_path / "results.json"
        results_file.write_text(json.dumps({
            "results": [
                {"name": "agent1", "api_key": "key1"},
            ]
        }))

        agents = load_registration_results(results_file)
        assert len(agents) == 1
        assert agents[0]["name"] == "agent1"

    def test_load_from_single_agent_dict(self, tmp_path):
        """Test loading from single agent dict."""
        results_file = tmp_path / "results.json"
        results_file.write_text(json.dumps({
            "name": "agent1",
            "api_key": "key1",
        }))

        agents = load_registration_results(results_file)
        assert len(agents) == 1
        assert agents[0]["name"] == "agent1"

    def test_load_from_invalid_json(self, tmp_path):
        """Test loading from invalid JSON."""
        results_file = tmp_path / "results.json"
        results_file.write_text("not valid json")

        with pytest.raises(json.JSONDecodeError):
            load_registration_results(results_file)

    def test_load_from_unsupported_format(self, tmp_path):
        """Test loading from unsupported format."""
        results_file = tmp_path / "results.json"
        results_file.write_text(json.dumps("string value"))

        with pytest.raises(ValueError, match="Unexpected format"):
            load_registration_results(results_file)


class TestParseRegistrationOutput:
    """Test parsing registration output from script stdout."""

    def test_parse_single_agent(self):
        """Test parsing single agent from output."""
        output = """
2024-01-01 10:00:00 - register_agents - INFO - Registering agent: test-agent
2024-01-01 10:00:01 - register_agents - INFO - Agent 'test-agent' registered successfully
2024-01-01 10:00:01 - register_agents - INFO -   API Key: botburrow_agent_abc123def456
"""
        agents = parse_registration_output(output)
        assert len(agents) == 1
        assert agents[0]["name"] == "test-agent"
        assert agents[0]["api_key"] == "botburrow_agent_abc123def456"

    def test_parse_multiple_agents(self):
        """Test parsing multiple agents from output."""
        output = """
Agent 'agent1' registered successfully
  API Key: botburrow_agent_key1
Agent 'agent2' registered successfully
  API Key: botburrow_agent_key2
"""
        agents = parse_registration_output(output)
        assert len(agents) == 2
        assert agents[0]["name"] == "agent1"
        assert agents[1]["name"] == "agent2"

    def test_parse_no_agents(self):
        """Test parsing output with no agents."""
        output = "No agents found in repository\n"
        agents = parse_registration_output(output)
        assert len(agents) == 0

    def test_parse_mixed_output(self):
        """Test parsing mixed output with logs and agent info."""
        output = """
2024-01-01 10:00:00 - INFO - Starting registration
Found 2 agent(s) in repository
Agent 'test-agent' registered successfully
  API Key: botburrow_agent_test123
2024-01-01 10:00:01 - INFO - Registration complete
"""
        agents = parse_registration_output(output)
        assert len(agents) == 1
        assert agents[0]["name"] == "test-agent"

    def test_parse_empty_output(self):
        """Test parsing empty output."""
        agents = parse_registration_output("")
        assert len(agents) == 0


class TestIntegrationScenarios:
    """Integration test scenarios for webhook workflow."""

    @patch('ci_webhook_sender.requests.post')
    def test_full_webhook_workflow(self, mock_post):
        """Test complete workflow from file load to webhook send."""
        # Mock webhook response
        mock_response = Mock()
        mock_response.json.return_value = {
            "success": True,
            "message": "Secrets created successfully",
            "repository": "https://github.com/test/repo.git",
            "commit_sha": "abc123",
            "commit_info": {
                "branch": "main",
                "commit_sha": "def456",
            },
            "secrets_created": [
                {"agent_name": "agent1", "secret_name": "agent-agent1", "success": True},
                {"agent_name": "agent2", "secret_name": "agent-agent2", "success": True},
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        # Prepare registration results
        agents = [
            {"name": "agent1", "api_key": "botburrow_agent_key1"},
            {"name": "agent2", "api_key": "botburrow_agent_key2"},
        ]

        # Send webhook
        result = send_webhook(
            webhook_url="https://botburrow.example.com/api/v1/webhooks/agent-registration",
            webhook_secret="test-secret",
            repository="https://github.com/test/repo.git",
            branch="main",
            commit_sha="abc123",
            agents=agents,
        )

        # Verify result
        assert result["success"] is True
        assert len(result["secrets_created"]) == 2
        assert all(s["success"] for s in result["secrets_created"])

    def test_signature_verification_workflow(self):
        """Test that signature verification would work on receiving end."""
        webhook_secret = "test-webhook-secret"
        agents = [{"name": "agent1", "api_key": "key1"}]

        # Simulate webhook payload creation
        payload = {
            "repository": "https://github.com/test/repo.git",
            "branch": "main",
            "commit_sha": "abc123",
            "timestamp": "2024-01-01T00:00:00",
            "agents": agents,
        }
        payload_json = json.dumps(payload, separators=(",", ":"))
        payload_bytes = payload_json.encode("utf-8")

        # Generate signature
        signature = generate_signature(payload_bytes, webhook_secret)

        # Simulate receiver verification
        expected_sig = hmac.new(
            webhook_secret.encode(),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

        assert signature == f"sha256={expected_sig}"

        # Verify format
        assert signature.startswith("sha256=")
        assert len(signature.split("=")[1]) == 64  # SHA256 hex length


class TestErrorHandling:
    """Test error handling in webhook operations."""

    @patch('ci_webhook_sender.requests.post')
    def test_webhook_server_error_response(self, mock_post):
        """Test handling of server error responses."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500 Server Error: Internal Server Error"
        )
        mock_post.return_value = mock_response

        agents = [{"name": "agent1", "api_key": "key1"}]

        with pytest.raises(requests.exceptions.HTTPError):
            send_webhook(
                webhook_url="https://botburrow.example.com/api/v1/webhooks/agent-registration",
                webhook_secret="test-secret",
                repository="https://github.com/test/repo.git",
                branch="main",
                commit_sha="abc123",
                agents=agents,
            )

    @patch('ci_webhook_sender.requests.post')
    def test_webhook_with_retry_simulation(self, mock_post):
        """Test behavior when webhook fails (no automatic retry in current impl)."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        agents = [{"name": "agent1", "api_key": "key1"}]

        # Current implementation doesn't retry, just raises
        with pytest.raises(requests.exceptions.RequestException):
            send_webhook(
                webhook_url="https://botburrow.example.com/api/v1/webhooks/agent-registration",
                webhook_secret="test-secret",
                repository="https://github.com/test/repo.git",
                branch="main",
                commit_sha="abc123",
                agents=agents,
            )
