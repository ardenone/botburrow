#!/usr/bin/env python3
"""
OpenBao KV v2 delivery for agent API keys.

Agent API keys are generated server-side by the Hub and returned exactly
once in the registration response. The Hub stores only a SHA-256 hash of
each key, so if the plaintext is not delivered to OpenBao immediately it
is unrecoverable.

This module delivers keys to OpenBao and verifies each write by property
(a metadata version bump) — never by reading the value back, and never by
emitting the value to logs or stdout. The generated key travels only inside
an HTTPS request body: never in argv, never in a log line, never in an error
message.

Configuration (environment variables, all overridable via constructor):

    OPENBAO_ADDR / BAO_ADDR        OpenBao instance address
    OPENBAO_TOKEN / BAO_TOKEN      Provisioning identity token
    OPENBAO_TOKEN_FILE / BAO_TOKEN_FILE
                                   File holding the token (mode 600);
                                   preferred over the plain env var

The token must belong to a provisioning identity with create/update on the
target KV data path and read access to the corresponding metadata path. It
does not need permission to read KV data. This module never reads secrets
back.
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# KV v2 field holding the agent API key
API_KEY_FIELD = "api-key"

# Default KV mount and path prefix (openbao-v2 instance owns
# secret/ardenone-cluster/*; botburrow runs on ardenone-cluster)
DEFAULT_KV_MOUNT = "secret"
DEFAULT_PATH_PREFIX = "ardenone-cluster/botburrow/agents"
DEFAULT_ADDR = "https://openbao.ardenone.com"


class OpenBaoDeliveryError(RuntimeError):
    """A key could not be delivered to (or verified in) OpenBao.

    The message intentionally never contains secret material — only the
    KV path, the address, and remediation hints.
    """


@dataclass
class OpenBaoReference:
    """A by-reference pointer to a delivered key. Safe to log, print,
    write into reports, or send in webhook payloads."""

    mount: str
    path: str
    field: str = API_KEY_FIELD

    @property
    def full_path(self) -> str:
        """KV v2 path including the mount, e.g. secret/ardenone-cluster/..."""
        return f"{self.mount}/{self.path}"

    def retrieval_hint(self, addr: str) -> str:
        """Human-readable retrieval recipe for this reference."""
        return (
            f"bao kv get -field={self.field} {self.full_path} (at {addr})"
        )


class OpenBaoClient:
    """Minimal KV v2 write client for OpenBao.

    Only implements what key delivery needs: a CAS write plus metadata
    reads for verification. Values are never logged, never returned to
    callers for display, and never placed in argv.
    """

    def __init__(
        self,
        addr: Optional[str] = None,
        token: Optional[str] = None,
        token_file: Optional[str] = None,
        mount: str = DEFAULT_KV_MOUNT,
        prefix: str = DEFAULT_PATH_PREFIX,
        timeout: int = 10,
        session: Optional[requests.Session] = None,
    ):
        self.addr = (
            addr
            or os.environ.get("OPENBAO_ADDR")
            or os.environ.get("BAO_ADDR")
            or DEFAULT_ADDR
        ).rstrip("/")
        self.mount = mount.strip("/")
        self.prefix = prefix.strip("/")
        self.timeout = timeout
        self._session = session

        resolved_token_file = (
            token_file
            or os.environ.get("OPENBAO_TOKEN_FILE")
            or os.environ.get("BAO_TOKEN_FILE")
        )
        if token:
            self._token = token
        elif resolved_token_file:
            self._token = self._read_token_file(resolved_token_file)
        else:
            self._token = (
                os.environ.get("OPENBAO_TOKEN")
                or os.environ.get("BAO_TOKEN")
            )

        if not self._token:
            raise OpenBaoDeliveryError(
                "No OpenBao token configured. Set OPENBAO_TOKEN_FILE (preferred, "
                "mode-600 file) or OPENBAO_TOKEN to a provisioning-identity "
                "token with create/update on the target KV path."
            )

    @staticmethod
    def _read_token_file(path: str) -> str:
        try:
            with open(path) as f:
                token = f.read().strip()
        except OSError as e:
            raise OpenBaoDeliveryError(
                f"Cannot read OpenBao token file {path}: {e}"
            )
        if not token:
            raise OpenBaoDeliveryError(
                f"OpenBao token file {path} is empty"
            )
        return token

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        # Keep the token in the request header, never in a URL or command
        # argument. ``setdefault`` preserves headers on injected test or
        # application sessions while ensuring the client is authenticated.
        self._session.headers.setdefault("X-Vault-Token", self._token)
        return self._session

    def agent_ref(self, agent_name: str) -> OpenBaoReference:
        """Build the reference for an agent's API key path."""
        return OpenBaoReference(
            mount=self.mount,
            path=f"{self.prefix}/{agent_name}",
        )

    def _api_url(self, ref: OpenBaoReference) -> str:
        return f"{self.addr}/v1/{ref.mount}/data/{ref.path}"

    def _metadata_url(self, ref: OpenBaoReference) -> str:
        return f"{self.addr}/v1/{ref.mount}/metadata/{ref.path}"

    def _current_version(self, ref: OpenBaoReference) -> int:
        """Read the path's current version from metadata.

        Returns 0 when the path does not exist yet. Raises with a
        remediation message if metadata cannot be read.
        """
        session = self._get_session()
        try:
            response = session.get(self._metadata_url(ref), timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise OpenBaoDeliveryError(
                f"Cannot reach OpenBao at {self.addr} to read metadata for "
                f"{ref.full_path}: {e}"
            )
        if response.status_code == 404:
            return 0
        if response.status_code == 403:
            raise OpenBaoDeliveryError(
                f"OpenBao denied metadata read for {ref.full_path} (403). "
                "Use a provisioning identity with list/read on metadata "
                "for this prefix."
            )
        if response.status_code != 200:
            raise OpenBaoDeliveryError(
                f"Unexpected status {response.status_code} reading metadata "
                f"for {ref.full_path}"
            )
        version = response.json().get("data", {}).get("current_version", 0)
        return int(version or 0)

    def deliver(self, agent_name: str, api_key: str) -> OpenBaoReference:
        """Deliver an API key to OpenBao and verify the write by property.

        The write uses CAS (compare-and-set) against the version observed
        in metadata, so a concurrent writer is rejected instead of being
        silently overwritten. Success is verified by the metadata version
        bumping by exactly one — the value is never read back.

        Raises OpenBaoDeliveryError on any failure. The error message
        never contains the key.
        """
        if not api_key:
            raise OpenBaoDeliveryError("Refusing to deliver an empty API key")

        ref = self.agent_ref(agent_name)
        session = self._get_session()

        cas = self._current_version(ref)

        payload = {
            "data": {ref.field: api_key},
            "options": {"cas": cas},
        }
        try:
            response = session.post(self._api_url(ref), json=payload, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise OpenBaoDeliveryError(
                f"Cannot reach OpenBao at {self.addr} to write "
                f"{ref.full_path}: {e}"
            )

        if response.status_code == 400 and "check-and-set" in response.text:
            raise OpenBaoDeliveryError(
                f"CAS mismatch writing {ref.full_path} (expected version "
                f"{cas}). The path was written concurrently; re-read "
                "metadata and retry."
            )
        if response.status_code == 403:
            raise OpenBaoDeliveryError(
                f"OpenBao denied the write to {ref.full_path} (403). The "
                "token needs create/update capability on this path — use "
                "a provisioning identity."
            )
        if response.status_code != 200:
            raise OpenBaoDeliveryError(
                f"Unexpected status {response.status_code} writing "
                f"{ref.full_path}"
            )

        new_version = (
            response.json().get("data", {}).get("version")
        )

        # Property verification: metadata must report exactly cas+1.
        verified_version = self._current_version(ref)
        if verified_version != cas + 1:
            raise OpenBaoDeliveryError(
                f"Write to {ref.full_path} could not be verified: metadata "
                f"reports version {verified_version}, expected {cas + 1} "
                f"(write response reported {new_version})."
            )

        logger.info(
            f"API key delivered to OpenBao: {ref.full_path} "
            f"(field: {ref.field}, version: {verified_version})"
        )
        return ref
