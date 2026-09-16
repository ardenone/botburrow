"""Tests for OpenBao KV delivery and property-based verification."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from openbao_store import OpenBaoClient, OpenBaoDeliveryError


def response(status_code, payload=None, text=""):
    result = Mock(status_code=status_code, text=text)
    result.json.return_value = payload or {}
    return result


def client_with_session(session):
    return OpenBaoClient(
        addr="https://openbao.example",
        token="provisioning-token",
        mount="secret",
        prefix="cluster/botburrow/agents",
        session=session,
    )


class TestOpenBaoDelivery:
    def test_writes_key_and_verifies_metadata_bump(self):
        session = Mock()
        session.get.side_effect = [
            response(404),
            response(200, {"data": {"current_version": 1}}),
        ]
        session.post.return_value = response(200, {"data": {"version": 1}})

        ref = client_with_session(session).deliver("my-agent", "one-time-key")

        assert ref.full_path == "secret/cluster/botburrow/agents/my-agent"
        assert session.get.call_count == 2
        assert session.post.call_args.kwargs["json"] == {
            "data": {"api-key": "one-time-key"},
            "options": {"cas": 0},
        }
        assert session.get.call_args_list[1].kwargs["timeout"] == 10

    def test_never_treats_write_response_as_verification(self):
        session = Mock()
        session.get.side_effect = [
            response(404),
            response(200, {"data": {"current_version": 2}}),
        ]
        session.post.return_value = response(200, {"data": {"version": 1}})

        with pytest.raises(OpenBaoDeliveryError, match="could not be verified") as exc:
            client_with_session(session).deliver("my-agent", "one-time-key")

        assert "one-time-key" not in str(exc.value)

    def test_rejects_empty_key(self):
        with pytest.raises(OpenBaoDeliveryError, match="empty API key"):
            client_with_session(Mock()).deliver("my-agent", "")
