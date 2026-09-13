"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from proxy import __version__
from proxy.config import get_settings
from proxy.models import read_catalog
from proxy.proxy_service import ProxyService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle - start/stop the proxy."""
    await app.state.proxy.start()
    try:
        yield
    finally:
        await app.state.proxy.close()


def create_app(
    catalog: dict[str, Any] | None = None,
    modified_at: str = "",
    *,
    daemon_url: str | None = None,
    serve_url: str | None = None,
    token: str | None = None,
    idle_seconds: float | None = None,
    start_timeout_seconds: float | None = None,
    client: Any = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    When called without arguments, reads configuration from environment variables
    (via ``get_settings``).  When called with explicit keyword arguments the
    values override everything – this is the path used by tests that inject a
    mock upstream client.

    Use ``None`` (default) to fall back to environment variables.  Passing an
    explicit value – even zero, empty string, or False – overrides the setting.
    """
    settings = get_settings()

    catalog = catalog if catalog is not None else {}
    if not modified_at:
        catalog_path = settings.model_catalog
        if not catalog and (catalog_path or __import__("pathlib").Path("/app/models.json").exists()):
            try:
                catalog, modified_at = read_catalog(catalog_path)
            except FileNotFoundError:
                catalog, modified_at = {}, ""

    proxy = ProxyService(
        catalog,
        modified_at,
        daemon_url=daemon_url if daemon_url is not None else settings.daemon_url,
        serve_url=serve_url if serve_url is not None else settings.serve_url,
        token=token if token is not None else settings.token,
        idle_seconds=idle_seconds if idle_seconds is not None else settings.idle_seconds,
        start_timeout_seconds=start_timeout_seconds if start_timeout_seconds is not None else settings.start_timeout_seconds,
        client=client,
    )

    app = FastAPI(
        title="FreeToken Ollama/OpenAI proxy",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.proxy = proxy

    # Register routers
    from proxy.routers import engine, ollama, openai

    app.include_router(engine.router)
    app.include_router(ollama.router, prefix="")
    app.include_router(openai.router, prefix="")

    return app


# Module-level instance for uvicorn
app = create_app()


def main() -> None:
    """CLI entry point – start the uvicorn server."""
    import uvicorn

    uvicorn.run(
        "proxy.main:app",
        host="0.0.0.0",
        port=11434,
        log_level="info",
    )
