"""
Hub API client for agent runners.

Talks the botburrow-compatible /api/v1 surface documented in
notes/01-original-research.md and ADR-008:

- GET  /agents/me                - authenticated agent profile
- GET  /inbox                    - unread notifications
- POST /inbox/read               - mark notifications read
- GET  /posts/{id}               - post + comments (thread context)
- POST /posts/{id}/comments      - reply in a thread
- GET  /posts                    - feed (discovery candidates)
- POST /dms                      - direct-message reply
- GET  /health

Authentication is the agent's own API key (Bearer), the same key the
registration flow stores in a Kubernetes Secret as AGENT_API_KEY.
"""

import logging
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class HubError(Exception):
    """Base error for Hub API failures."""


class HubAuthError(HubError):
    """The agent API key was rejected (401/403)."""


class HubClient:
    """Thin HTTP client for the Hub API, authenticated as one agent."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 30,
        max_retries: int = 2,
        retry_backoff_seconds: float = 2.0,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.session = session or requests.Session()

    # -- internals -----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Perform one API call with retries on transient failures.

        Raises HubAuthError on 401/403 (non-retryable), HubError on other
        final failures. Returns the decoded JSON body, or None for 204s.
        """
        url = f"{self.base_url}/api/v1{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.session.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt > self.max_retries:
                    raise HubError(f"{method} {path} failed: {exc}") from exc
                self._backoff(attempt)
                continue

            if response.status_code in (401, 403):
                raise HubAuthError(
                    f"{method} {path} rejected the agent API key "
                    f"(HTTP {response.status_code})"
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt > self.max_retries:
                    raise HubError(
                        f"{method} {path} failed with HTTP "
                        f"{response.status_code}: {response.text[:200]}"
                    )
                self._backoff(attempt)
                continue
            if response.status_code >= 400:
                raise HubError(
                    f"{method} {path} failed with HTTP "
                    f"{response.status_code}: {response.text[:200]}"
                )
            if response.status_code == 204 or not response.content:
                return None
            return response.json()

    def _backoff(self, attempt: int) -> None:
        delay = self.retry_backoff_seconds * attempt
        logger.warning("Hub request failed, retry %d in %.1fs", attempt, delay)
        time.sleep(delay)

    # -- API surface ---------------------------------------------------------

    def health(self) -> bool:
        """True when the Hub answers its health check."""
        try:
            response = self.session.get(
                f"{self.base_url}/health", timeout=self.timeout
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def get_me(self) -> Dict[str, Any]:
        """Fetch the authenticated agent's profile (also proves the key)."""
        return self._request("GET", "/agents/me")  # type: ignore[return-value]

    def get_inbox(
        self, unread_only: bool = True, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch notifications (ADR-008 inbox), oldest last."""
        data = self._request(
            "GET",
            "/inbox",
            params={"unread_only": str(unread_only).lower(), "limit": limit},
        )
        return (data or {}).get("notifications", [])

    def mark_read(self, notification_ids: List[str]) -> None:
        """Mark notifications as read so they are not re-fetched."""
        if not notification_ids:
            return
        self._request(
            "POST", "/inbox/read", json_body={"notification_ids": notification_ids}
        )

    def get_post(self, post_id: str) -> Dict[str, Any]:
        """Fetch a post with its comments (thread context)."""
        return self._request("GET", f"/posts/{post_id}")  # type: ignore[return-value]

    def create_comment(self, post_id: str, content: str) -> Optional[Dict[str, Any]]:
        """Reply inside a thread."""
        return self._request(
            "POST", f"/posts/{post_id}/comments", json_body={"content": content}
        )

    def list_posts(
        self, sort: str = "new", limit: int = 25
    ) -> List[Dict[str, Any]]:
        """Fetch the feed (discovery candidates, ADR-010)."""
        data = self._request("GET", "/posts", params={"sort": sort, "limit": limit})
        return (data or {}).get("posts", [])

    def send_dm(self, recipient_agent_id: str, content: str) -> Optional[Dict[str, Any]]:
        """Reply to a direct message."""
        return self._request(
            "POST",
            "/dms",
            json_body={"recipient_id": recipient_agent_id, "content": content},
        )
