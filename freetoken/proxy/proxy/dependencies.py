"""FastAPI dependencies for the proxy."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request, HTTPException

from proxy.proxy_service import ProxyService


def find_model(proxy: Any, name: str | None) -> Any:
    """Lookup a model by name in the catalog, raising 404 if not found."""
    if not isinstance(name, str) or name not in proxy.catalog:
        raise HTTPException(404, f"model not found: {name}")
    return proxy.catalog[name]


async def json_body(request: Request) -> dict[str, Any]:
    """Parse and validate a JSON request body."""
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "invalid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be an object")
    return body


async def get_proxy(request: Request) -> ProxyService:
    """Dependency to inject the ProxyService from app.state."""
    proxy = request.app.state.proxy
    if proxy is None:
        raise HTTPException(503, "proxy not initialized")
    return proxy
