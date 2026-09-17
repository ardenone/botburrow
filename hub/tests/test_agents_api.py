"""
Integration tests for the agent registry endpoints (hub/api/v1/agents.py).

Covers the admin CRUD surface (GET /agents/{name}, GET /agents,
DELETE /agents/{name}), the agent self-service surface (PATCH
/agents/me, GET /agents/status — ADR-002), the admin/agent authz
matrix, and a route-ordering regression: static paths (/health) must be
declared before the /{agent_name} parameterized route, or they are
captured as agent_name="health".
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from botburrow_hub.api.v1.agents import hash_api_key
from botburrow_hub.config import settings

# Deliberately does NOT carry the botburrow_agent_ agent-key prefix, so
# the agent-endpoint tests exercise the wrong-credential-kind rejection
# (403 Invalid API key format) rather than a lucky hash match.
ADMIN_TOKEN = "botburrow_admin_" + uuid.uuid4().hex
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture()
def api(make_app, monkeypatch):
    """TestClient with admin auth configured against a throwaway DB."""
    app = make_app()  # resets admin_api_key_hash to None
    monkeypatch.setattr(settings, "admin_api_key_hash", hash_api_key(ADMIN_TOKEN))
    with TestClient(app) as client:
        yield client


def _register(client, name, **overrides) -> dict:
    """Register an agent with admin auth and return the response payload."""
    r = client.post(
        "/api/v1/agents/register", headers=ADMIN, json={"name": name, **overrides}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _agent_auth(client, name=None) -> dict:
    """Register a throwaway agent and return its API-key auth header."""
    key = _register(client, name or ("probe-" + uuid.uuid4().hex[:8]))["api_key"]
    return {"Authorization": f"Bearer {key}"}


# Every admin endpoint, as (http method, path template) pairs.
ADMIN_ROUTES = [
    ("get", "/api/v1/agents/{name}"),
    ("get", "/api/v1/agents"),
    ("delete", "/api/v1/agents/{name}"),
]


class TestAuthzMatrix:
    @pytest.mark.parametrize("method,path", ADMIN_ROUTES)
    def test_admin_endpoints_reject_anonymous(self, api, method, path):
        r = getattr(api, method)(path.format(name="some-agent"))
        assert r.status_code == 401

    @pytest.mark.parametrize("method,path", ADMIN_ROUTES)
    def test_admin_endpoints_reject_agent_keys(self, api, method, path):
        headers = _agent_auth(api)
        r = getattr(api, method)(path.format(name="some-agent"), headers=headers)
        assert r.status_code == 403

    def test_agent_endpoints_reject_anonymous(self, api):
        assert api.get("/api/v1/agents/status").status_code == 401
        assert api.patch("/api/v1/agents/me", json={}).status_code == 401

    def test_agent_endpoints_reject_admin_token(self, api):
        # An admin token is not an agent API key: /status and PATCH /me
        # must not silently treat it as one.
        assert api.get("/api/v1/agents/status", headers=ADMIN).status_code == 403
        r = api.patch(
            "/api/v1/agents/me", headers=ADMIN, json={"display_name": "nope"}
        )
        assert r.status_code == 403


class TestGetAgentByName:
    def test_returns_registered_agent(self, api):
        source = "https://git.ardenone.com/jedarden/agents.git"
        _register(
            api,
            "get-me",
            display_name="Get Me",
            type="claude-code",
            config_source=source,
        )

        r = api.get("/api/v1/agents/get-me", headers=ADMIN)
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "get-me"
        assert body["display_name"] == "Get Me"
        assert body["type"] == "claude-code"
        assert body["config_source"] == source
        assert body["karma"] == 0
        assert body["is_admin"] is False
        assert "api_key" not in body  # hashes only ever leave the registry

    def test_404_for_unknown_agent(self, api):
        r = api.get("/api/v1/agents/who-is-this", headers=ADMIN)
        assert r.status_code == 404
        assert r.json()["detail"] == "Agent 'who-is-this' not found"


class TestListAgents:
    def test_empty_registry(self, api):
        r = api.get("/api/v1/agents", headers=ADMIN)
        assert r.status_code == 200
        assert r.json() == {"agents": [], "total": 0, "offset": 0, "limit": 100}

    def test_lists_registered_agents_sorted_by_name(self, api):
        names = [f"agent-{i}" for i in range(3)]
        for name in names:
            _register(api, name)

        r = api.get("/api/v1/agents", headers=ADMIN)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        assert [a["name"] for a in body["agents"]] == sorted(names)

    def test_offset_and_limit_paginate(self, api):
        for i in range(3):
            _register(api, f"agent-{i}")

        r = api.get(
            "/api/v1/agents", params={"offset": 1, "limit": 1}, headers=ADMIN
        )
        assert r.status_code == 200
        body = r.json()
        # total is the unpaginated count, not the page size
        assert body["total"] == 3
        assert body["offset"] == 1
        assert body["limit"] == 1
        assert [a["name"] for a in body["agents"]] == ["agent-1"]

    def test_config_source_filter(self, api):
        fleet = "https://git.ardenone.com/jedarden/fleet.git"
        _register(api, "in-fleet", config_source=fleet)
        _register(api, "elsewhere", config_source="https://example.com/other.git")

        r = api.get(
            "/api/v1/agents", params={"config_source": fleet}, headers=ADMIN
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert [a["name"] for a in body["agents"]] == ["in-fleet"]

    def test_filter_with_no_matches_has_zero_total(self, api):
        _register(api, "lone-agent")
        r = api.get(
            "/api/v1/agents",
            params={"config_source": "https://example.com/none.git"},
            headers=ADMIN,
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0
        assert r.json()["agents"] == []

    def test_negative_offset_rejected(self, api):
        assert (
            api.get("/api/v1/agents", params={"offset": -1}, headers=ADMIN).status_code
            == 422
        )


class TestDeleteAgent:
    def test_deletes_and_subsequent_get_404s(self, api):
        _register(api, "doomed")

        r = api.delete("/api/v1/agents/doomed", headers=ADMIN)
        assert r.status_code == 204
        assert api.get("/api/v1/agents/doomed", headers=ADMIN).status_code == 404

    def test_404_for_unknown_agent(self, api):
        r = api.delete("/api/v1/agents/never-existed", headers=ADMIN)
        assert r.status_code == 404
        assert r.json()["detail"] == "Agent 'never-existed' not found"


class TestPatchMe:
    def test_updates_editable_fields(self, api):
        key = _register(api, "patchy", description="kept")["api_key"]
        headers = {"Authorization": f"Bearer {key}"}

        r = api.patch(
            "/api/v1/agents/me",
            headers=headers,
            json={
                "display_name": "Patched",
                "avatar_url": "https://example.com/a.png",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["display_name"] == "Patched"
        assert body["avatar_url"] == "https://example.com/a.png"
        # Fields absent from the body keep their values (PATCH, not PUT).
        assert body["description"] == "kept"

        # Persisted, not just echoed back.
        me = api.get("/api/v1/agents/me", headers=headers).json()
        assert me["display_name"] == "Patched"
        assert me["avatar_url"] == "https://example.com/a.png"

    def test_explicit_null_clears_field(self, api):
        key = _register(api, "clearable", description="to be cleared")["api_key"]
        headers = {"Authorization": f"Bearer {key}"}

        r = api.patch(
            "/api/v1/agents/me", headers=headers, json={"description": None}
        )
        assert r.status_code == 200
        assert r.json()["description"] is None

    def test_empty_body_changes_nothing(self, api):
        key = _register(api, "untouched", description="same")["api_key"]
        headers = {"Authorization": f"Bearer {key}"}

        r = api.patch("/api/v1/agents/me", headers=headers, json={})
        assert r.status_code == 200
        assert r.json()["description"] == "same"

    @pytest.mark.parametrize("protected", [
        {"name": "renamed"},
        {"type": "goose"},
        {"is_admin": True},
        {"karma": 999},
        {"api_key": "botburrow_agent_deadbeef"},
        {"api_key_hash": "not-a-real-hash"},
        {"api_key_expires_at": "2030-01-01T00:00:00"},
        {"config_source": "https://example.com/evil.git"},
    ])
    def test_protected_fields_are_rejected(self, api, protected):
        key = _register(api, "guarded")["api_key"]
        headers = {"Authorization": f"Bearer {key}"}

        r = api.patch("/api/v1/agents/me", headers=headers, json=protected)
        assert r.status_code == 422

        # The rejection is not a silent no-op that also succeeded.
        profile = api.get("/api/v1/agents/me", headers=headers).json()
        assert profile["name"] == "guarded"
        assert profile["type"] == "native"
        assert profile["is_admin"] is False


class TestAgentStatus:
    def test_returns_lightweight_status(self, api):
        reg = _register(api, "poller", api_key_expires_at="2030-01-01T00:00:00Z")
        headers = {"Authorization": f"Bearer {reg['api_key']}"}

        r = api.get("/api/v1/agents/status", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "poller"
        assert body["type"] == "native"
        assert body["karma"] == 0
        assert body["is_admin"] is False
        assert body["last_active_at"] is None
        assert body["api_key_expires_at"] is not None
        # Exactly the polling-loop field set — no api_key, no config blob.
        assert set(body) == {
            "name",
            "type",
            "karma",
            "is_admin",
            "last_active_at",
            "api_key_expires_at",
        }

    def test_defaults_when_key_never_expires(self, api):
        key = _register(api, "immortal")["api_key"]
        headers = {"Authorization": f"Bearer {key}"}

        r = api.get("/api/v1/agents/status", headers=headers)
        assert r.status_code == 200
        assert r.json()["api_key_expires_at"] is None


class TestRouteOrdering:
    def test_agents_health_is_not_captured_by_name_route(self, api):
        """Regression: /health was declared after GET /{agent_name}. That
        was invisible while the name route was a 501 stub, but a real
        implementation would resolve /agents/health as
        agent_name="health" (401/404) instead of the health check."""
        r = api.get("/api/v1/agents/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["service"] == "botburrow-hub-agents"
        assert "timestamp" in body

    def test_name_route_still_works_after_health(self, api):
        # The reorder must not have broken the parameterized route.
        _register(api, "still-here")
        r = api.get("/api/v1/agents/still-here", headers=ADMIN)
        assert r.status_code == 200
        assert r.json()["name"] == "still-here"
