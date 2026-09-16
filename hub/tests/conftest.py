"""
Shared fixtures for Hub API integration tests.

botburrow_hub is a packaged alias of this repo's hub/ directory (see
pyproject.toml). When the package is installed the real import wins;
otherwise hub/ is aliased to botburrow_hub in sys.modules so the suite
runs straight from a checkout, matching how the rest of this repo runs
pytest with no install step. (The alias shares one module object, so
there is no hub.* vs botburrow_hub.* state duplication.)
"""

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    import botburrow_hub  # noqa: F401
except ImportError:
    # Execute hub/__init__.py under the botburrow_hub name. The alias must
    # be registered before the package body runs, because that body itself
    # does `from botburrow_hub.config import settings`.
    import importlib.util

    _init = _REPO_ROOT / "hub" / "__init__.py"
    _spec = importlib.util.spec_from_file_location(
        "botburrow_hub",
        _init,
        submodule_search_locations=[str(_REPO_ROOT / "hub")],
    )
    _alias = importlib.util.module_from_spec(_spec)
    sys.modules["botburrow_hub"] = _alias
    _spec.loader.exec_module(_alias)

from botburrow_hub.config import settings  # noqa: E402
from botburrow_hub.main import create_app  # noqa: E402


@pytest.fixture()
def make_app(tmp_path, monkeypatch):
    """Build the Hub app against a throwaway SQLite database."""

    def _make(**create_app_kwargs):
        monkeypatch.setattr(
            settings,
            "database_url",
            f"sqlite+aiosqlite:///{tmp_path / 'hub-test.db'}",
        )
        monkeypatch.setattr(settings, "api_prefix", "/api/v1")
        monkeypatch.setattr(settings, "admin_api_key_hash", None)
        monkeypatch.setattr(settings, "ci_webhook_secret", None)
        monkeypatch.setattr(settings, "cors_origins", ["http://localhost:3000"])
        return create_app(**create_app_kwargs)

    return _make


@pytest.fixture()
def app(make_app):
    """Hub app with a small rate budget so exhaustion is cheap to test."""
    return make_app(rate_limit_per_minute=5)


@pytest.fixture()
def client(app):
    """TestClient with lifespan already run (startup created the tables)."""
    with TestClient(app) as test_client:
        yield test_client
