"""
Tests for the Hub API client.

Uses a stubbed requests.Session — no network.
Covers auth headers, retries, error mapping, and endpoint shapes.
"""

import pytest
import requests

from runner.hub import HubAuthError, HubClient, HubError


class StubResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.content = b"x" if json_data is not None or text else b""

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class StubSession:
    """Records requests; returns queued responses (or a default)."""

    def __init__(self, responses=None):
        self.requests = []
        self.responses = list(responses or [])
        self.default = StubResponse(200, {})

    def request(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        if self.responses:
            return self.responses.pop(0)
        return self.default

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


def make_client(session, **kwargs):
    return HubClient(
        base_url="https://hub.example.com",
        api_key="agent-key-123",
        session=session,
        retry_backoff_seconds=0.0,
        **kwargs,
    )


class TestAuthAndHeaders:
    def test_bearer_header_sent(self):
        session = StubSession()
        client = make_client(session)
        client.get_me()
        headers = session.requests[0]["headers"]
        assert headers["Authorization"] == "Bearer agent-key-123"

    def test_url_prefix(self):
        session = StubSession()
        make_client(session).get_inbox()
        assert session.requests[0]["url"] == "https://hub.example.com/api/v1/inbox"


class TestErrorHandling:
    def test_401_raises_hub_auth_error(self):
        session = StubSession([StubResponse(401, {"detail": "nope"})])
        with pytest.raises(HubAuthError):
            make_client(session).get_me()

    def test_403_raises_hub_auth_error(self):
        session = StubSession([StubResponse(403, {})])
        with pytest.raises(HubAuthError):
            make_client(session).get_inbox()

    def test_404_raises_hub_error(self):
        session = StubSession([StubResponse(404, {}, text="not found")])
        with pytest.raises(HubError, match="404"):
            make_client(session).get_post("p1")

    def test_500_retries_then_raises(self):
        session = StubSession([StubResponse(500, {}, text="boom")] * 3)
        with pytest.raises(HubError, match="500"):
            make_client(session, max_retries=2).get_me()
        assert len(session.requests) == 3  # 1 initial + 2 retries

    def test_500_recovers_on_second_attempt(self):
        session = StubSession([StubResponse(500, {}, text="boom"),
                               StubResponse(200, {"id": "me"})])
        assert make_client(session, max_retries=2).get_me()["id"] == "me"

    def test_connection_error_retries_then_raises(self):
        session = StubSession()
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            raise requests.ConnectionError("down")

        session.request = flaky
        with pytest.raises(HubError):
            make_client(session, max_retries=1).get_me()
        assert calls["n"] == 2


class TestEndpoints:
    def test_get_inbox_parses_notifications(self):
        session = StubSession([StubResponse(200, {
            "notifications": [{"id": "n1", "type": "mention"}],
            "unread_count": 1,
        })])
        notifications = make_client(session).get_inbox(unread_only=True, limit=10)
        assert notifications[0]["id"] == "n1"
        assert session.requests[0]["params"] == {"unread_only": "true", "limit": 10}

    def test_mark_read_posts_ids(self):
        session = StubSession()
        make_client(session).mark_read(["n1", "n2"])
        req = session.requests[0]
        assert req["method"] == "POST"
        assert req["url"].endswith("/inbox/read")
        assert req["json"] == {"notification_ids": ["n1", "n2"]}

    def test_mark_read_noop_on_empty(self):
        session = StubSession()
        make_client(session).mark_read([])
        assert session.requests == []

    def test_create_comment(self):
        session = StubSession([StubResponse(201, {"id": "c1"})])
        result = make_client(session).create_comment("post-1", "hello")
        req = session.requests[0]
        assert req["url"].endswith("/posts/post-1/comments")
        assert req["json"] == {"content": "hello"}
        assert result["id"] == "c1"

    def test_list_posts(self):
        session = StubSession([StubResponse(200, {"posts": [{"id": "p1"}]})])
        posts = make_client(session).list_posts(sort="new", limit=25)
        assert posts[0]["id"] == "p1"
        assert session.requests[0]["params"] == {"sort": "new", "limit": 25}

    def test_send_dm(self):
        session = StubSession()
        make_client(session).send_dm("agent-9", "hi")
        req = session.requests[0]
        assert req["url"].endswith("/dms")
        assert req["json"] == {"recipient_id": "agent-9", "content": "hi"}

    def test_get_post(self):
        session = StubSession([StubResponse(200, {"id": "p1", "comments": []})])
        post = make_client(session).get_post("p1")
        assert post["id"] == "p1"

    def test_health_uses_unauthenticated_root(self):
        session = StubSession()
        session.default = StubResponse(200, {"status": "ok"})
        assert make_client(session).health() is True

        failing = StubSession()
        failing.get = lambda *a, **k: (_ for _ in ()).throw(
            requests.ConnectionError("down"))
        assert make_client(failing).health() is False

    def test_204_returns_none(self):
        session = StubSession([StubResponse(204)])
        assert make_client(session).mark_read(["n1"]) is None
