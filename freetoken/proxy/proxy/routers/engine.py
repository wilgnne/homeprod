"""Engine management routes - health, version, catalog, ps."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from starlette.responses import JSONResponse

from proxy.dependencies import find_model, get_proxy, json_body
from proxy.schemas import EngineStatusResponse, PsModelResponse, TagResponse
from proxy.utils import keep_alive_seconds, model_details, tag_info, utc_now
from proxy.proxy_service import ProxyService

router = APIRouter()


@router.get("/")
async def root() -> str:
    return "Ollama is running"


@router.get("/api/version")
async def version(proxy: ProxyService = Depends(get_proxy)) -> dict[str, str]:
    from proxy import __version__
    return {"version": __version__}


@router.get("/api/tags")
async def tags(proxy: ProxyService = Depends(get_proxy)) -> dict[str, list[dict[str, Any]]]:
    return {
        "models": [
            tag_info(spec, proxy.modified_at) for spec in proxy.catalog.values()
        ]
    }


@router.get("/v1/models")
async def openai_models(proxy: ProxyService = Depends(get_proxy)) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": spec.name,
                "object": "model",
                "created": int(datetime.fromisoformat(proxy.modified_at).timestamp()),
                "owned_by": "freetoken",
            }
            for spec in proxy.catalog.values()
        ],
    }


@router.get("/v1/models/{model_id:path}")
async def openai_model_detail(model_id: str, proxy: ProxyService = Depends(get_proxy)) -> dict[str, Any]:
    spec = proxy.catalog.get(model_id)
    if spec is None:
        raise HTTPException(404, f"model not found: {model_id}")
    return {
        "id": spec.name,
        "object": "model",
        "created": int(datetime.fromisoformat(proxy.modified_at).timestamp()),
        "owned_by": "freetoken",
    }


@router.post("/api/show")
async def show(request: Request, proxy: ProxyService = Depends(get_proxy)) -> dict[str, Any]:
    body = await json_body(request)
    model_name = body.get("model") or body.get("name")
    spec = find_model(proxy, model_name)
    return {
        "details": model_details(spec),
        "model_info": {},
        "capabilities": ["completion"],
        "modified_at": proxy.modified_at,
    }


@router.get("/api/ps")
async def ps(proxy: ProxyService = Depends(get_proxy)) -> dict[str, Any]:
    st = await proxy._daemon("GET", "/engine/status")
    if not st.get("running"):
        return {"models": []}
    spec = next(
        (s for s in proxy.catalog.values() if s.model == st.get("model")),
        None,
    )
    if spec is None:
        return {"models": []}
    try:
        health = await proxy.client.get(
            proxy.serve_url + "/health", timeout=3
        )
        report = health.json() if health.status_code == 200 else {}
    except Exception:
        return {"models": []}
    if not isinstance(report, dict):
        return {"models": []}
    if report.get("status") != "ok" or report.get("maintenance", "serving") != "serving":
        return {"models": []}

    if proxy.active or not proxy.deadline_initialized:
        remaining = proxy.idle_seconds
        expires_at = datetime.fromtimestamp(
            time.time() + remaining, timezone.utc
        ).isoformat().replace("+00:00", "Z")
    elif proxy.deadline is None:
        expires_at = "9999-12-31T23:59:59Z"
    else:
        remaining = max(0, proxy.deadline - time.monotonic())
        expires_at = datetime.fromtimestamp(
            time.time() + remaining, timezone.utc
        ).isoformat().replace("+00:00", "Z")

    entry = tag_info(spec, proxy.modified_at)
    entry["size_vram"] = 0

    entry["expires_at"] = expires_at
    return {"models": [entry]}
