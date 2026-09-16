"""
Integration tests for the assembled Hub application (hub/main.py).

Exercises the real router stack through fastapi.testclient (httpx):
routers mounted under /api/v1, the unprefixed /health probe, CORS, and
the general rate limiter's X-RateLimit-* headers (ADR-002).
"""

import uuid

TEST_ORIGIN = "http://localhost:3000"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer botburrow_agent_{token}"}


class TestAppAssembly:
    def test_health_is_unprefixed(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "service": "botburrow-hub"}

    def test_agents_router_mounted_under_prefix(self, client):
        # The existing router's authenticated profile route proves that the
        # router is present without changing agent-admin behavior here.
        r = client.get("/api/v1/agents/me")
        assert r.status_code == 401

    def test_webhooks_router_mounted_under_prefix(self, client):
        r = client.post("/api/v1/webhooks/ping")
        assert r.status_code == 200

    def test_agents_me_requires_auth(self, client):
        # Proves the router's DB-backed auth dependency is wired end to end
        # (startup created the tables; the request fails authentication).
        r = client.get("/api/v1/agents/me")
        assert r.status_code == 401

    def test_unknown_api_route_is_404(self, client):
        assert client.get("/api/v1/does-not-exist").status_code == 404


class TestCors:
    def test_allowed_origin_echoed(self, client):
        r = client.get("/health", headers={"Origin": TEST_ORIGIN})
        assert r.headers["access-control-allow-origin"] == TEST_ORIGIN

    def test_unlisted_origin_gets_no_allow_header(self, client):
        r = client.get("/health", headers={"Origin": "http://evil.example.com"})
        assert "access-control-allow-origin" not in r.headers

    def test_preflight_allowed(self, client):
        r = client.options(
            "/api/v1/agents/me",
            headers={
                "Origin": TEST_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == TEST_ORIGIN


class TestRateLimiting:
    def _freeze_clock(self, app, client, start=1_000_000.0):
        """Pin the limiter's clock so window math is deterministic.

        The initial /health request builds the middleware stack (which
        registers app.state.rate_limiter) without touching any bucket.
        """
        client.get("/health")
        now = [start]
        app.state.rate_limiter.clock = lambda: now[0]
        return now

    @staticmethod
    def _ping(client, token=None):
        headers = auth(token) if token is not None else None
        return client.post("/api/v1/webhooks/ping", headers=headers)

    def test_headers_present_and_decrementing(self, app, client):
        self._freeze_clock(app, client)
        token = uuid.uuid4().hex

        r1 = self._ping(client, token)
        r2 = self._ping(client, token)

        assert r1.headers["x-ratelimit-limit"] == "5"
        assert r1.headers["x-ratelimit-remaining"] == "4"
        assert r2.headers["x-ratelimit-remaining"] == "3"
        # 1_000_000.0 falls in the window starting at 999_960 (60s window).
        assert r1.headers["x-ratelimit-reset"] == "1000020"
        assert r2.headers["x-ratelimit-reset"] == "1000020"

    def test_429_when_budget_exhausted(self, app, client):
        self._freeze_clock(app, client)
        token = uuid.uuid4().hex

        for _ in range(5):
            r = self._ping(client, token)
            assert r.status_code == 200
        assert r.headers["x-ratelimit-remaining"] == "0"

        blocked = self._ping(client, token)
        assert blocked.status_code == 429
        assert blocked.json()["detail"].startswith("Rate limit exceeded")
        assert blocked.headers["x-ratelimit-limit"] == "5"
        assert blocked.headers["x-ratelimit-remaining"] == "0"
        assert blocked.headers["x-ratelimit-reset"] == "1000020"
        assert blocked.headers["retry-after"] == "20"

    def test_budget_renews_when_window_rolls(self, app, client):
        now = self._freeze_clock(app, client)
        token = uuid.uuid4().hex

        for _ in range(6):
            self._ping(client, token)

        now[0] += 60  # cross into the next window
        r = self._ping(client, token)
        assert r.status_code == 200
        assert r.headers["x-ratelimit-remaining"] == "4"

    def test_api_keys_get_isolated_buckets(self, app, client):
        self._freeze_clock(app, client)

        r1 = self._ping(client, "alpha")
        r2 = self._ping(client, "beta")

        assert r1.headers["x-ratelimit-remaining"] == "4"
        assert r2.headers["x-ratelimit-remaining"] == "4"

    def test_unauthenticated_requests_bucket_by_ip(self, app, client):
        self._freeze_clock(app, client)

        default_ip = self._ping(client)
        other_ip = client.post(
            "/api/v1/webhooks/ping", headers={"X-Forwarded-For": "10.9.8.7"}
        )

        # The TestClient's peer address and the forwarded IP are different
        # buckets, so each starts with a full budget.
        assert default_ip.headers["x-ratelimit-remaining"] == "4"
        assert other_ip.headers["x-ratelimit-remaining"] == "4"

    def test_exempt_paths_get_no_headers_and_no_budget_cost(self, app, client):
        for _ in range(10):  # far more than the 5-request budget
            r = client.get("/health")
            assert r.status_code == 200
            assert "x-ratelimit-limit" not in r.headers

    def test_preflight_is_not_rate_limited(self, app, client):
        self._freeze_clock(app, client)
        for _ in range(6):  # would exhaust the budget if counted
            r = client.options(
                "/api/v1/agents/me",
                headers={
                    "Origin": TEST_ORIGIN,
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert r.status_code == 200
            assert "x-ratelimit-limit" not in r.headers

    def test_disabled_limiter_has_no_headers_or_budget(self, make_app):
        from fastapi.testclient import TestClient

        app = make_app(rate_limit_enabled=False)
        with TestClient(app) as client:
            for _ in range(7):  # more than the default 5 would allow
                r = client.post("/api/v1/webhooks/ping")
                assert r.status_code == 200
                assert "x-ratelimit-limit" not in r.headers
