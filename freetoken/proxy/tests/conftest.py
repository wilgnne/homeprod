"""Shared test fixtures."""

import json
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio

from proxy.config import Settings
from proxy.main import create_app
from proxy.models import Model, read_catalog
from proxy.proxy_service import ProxyService
from tests.fake_upstream import FakeUpstream


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Provide a clean settings instance for tests."""
    os = __import__("os")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '{"models": [{"name": "test/model", "model": "test/model", "args": []}]}'
    )
    return Settings()


@pytest.fixture
def catalog_path(tmp_path):
    """Create a valid catalog file for tests."""
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        json.dumps({
            "models": [
                {"name": "test/model", "model": "test/model", "args": []},
                {"name": "test/other", "model": "test/other", "args": ["--extra", "arg"]},
            ]
        })
    )
    return str(catalog_file)


def _make_proxy(catalog_path):
    """Create a ProxyService without any upstream connections."""
    catalog, modified_at = read_catalog(catalog_path)
    proxy = ProxyService(
        catalog,
        modified_at,
        daemon_url="http://mock-daemon:19000",
        serve_url="http://mock-serve:1919",
        token="test-token",
    )
    return proxy


@pytest.fixture
def app(catalog_path):
    """Create test app with minimal dependencies."""
    with patch.dict(
        "os.environ",
        {
            "FT_MODEL_CATALOG": catalog_path,
            "FT_DAEMON_URL": "http://mock-daemon:19000",
            "FT_SERVE_URL": "http://mock-serve:1919",
            "FT_DAEMON_TOKEN": "test-token",
        },
        clear=True,
    ):
        proxy = _make_proxy(catalog_path)
        app = create_app()
        app.state.proxy = proxy
        yield app


@pytest_asyncio.fixture
async def system(catalog_path):
    """Create a proxy app backed by a FakeUpstream transport."""
    fake = FakeUpstream()
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(fake.handle),
        base_url="http://daemon",
    )
    app = create_app(
        {"gemma": Model("gemma", "google/gemma-4-E2B"), "other": Model("other", "other/model")},
        "2026-01-01T00:00:00Z",
        daemon_url="http://daemon",
        serve_url="http://serve",
        client=upstream,
        idle_seconds=0.1,
        start_timeout_seconds=0.05,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy"
        ) as client:
            yield client, fake, app
    await upstream.aclose()
