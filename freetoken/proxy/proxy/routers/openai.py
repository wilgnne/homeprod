"""OpenAI API routes - chat, completions, responses."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response, StreamingResponse

from proxy.dependencies import find_model, json_body, get_proxy
from proxy.schemas import ErrorResponse
from proxy.proxy_service import ProxyService
from proxy.utils import keep_alive_seconds, openai_alias, sse_json

router = APIRouter()


async def _openai_inference(
    proxy: ProxyService,
    spec: Any,
    path: str,
    payload: dict[str, Any],
    stream: bool,
    duration: float | None,
) -> Any:
    """Forward an OpenAI request and handle the response."""
    released: list[bool] = [False]

    async def finish() -> None:
        if released[0]:
            return
        released[0] = True
        try:
            await response.aclose()
        finally:
            await proxy.release(duration)

    try:
        response = await proxy.upstream(path, payload, stream, raise_on_error=False)
    except asyncio.CancelledError:
        if released[0]:
            raise
        released[0] = True
        await proxy.release(duration)
        raise
    except Exception:
        if released[0]:
            raise
        released[0] = True
        await proxy.release(duration)
        raise

    if not stream or response.status_code >= 400:
        try:
            content = await response.aread()
            if response.status_code < 400 and "json" in response.headers.get("content-type", ""):
                try:
                    content = json.dumps(
                        openai_alias(json.loads(content), spec.name),
                        ensure_ascii=False,
                    ).encode()
                except ValueError:
                    pass
            return Response(
                content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type"),
            )
        finally:
            await finish()

    async def chunks() -> AsyncIterator[bytes]:
        try:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data and data != "[DONE]":
                        try:
                            line = "data: " + json.dumps(
                                openai_alias(json.loads(data), spec.name),
                                ensure_ascii=False,
                            )
                        except ValueError:
                            pass
                yield (line + "\n").encode()
        finally:
            await finish()

    return StreamingResponse(
        chunks(),
        headers={"content-type": response.headers.get("content-type", "text/event-stream")},
    )


async def _openai_request(
    request: Request,
    path: str,
    proxy: ProxyService,
) -> Any:
    """Generic handler for OpenAI-style endpoints."""
    body = await json_body(request)
    spec = find_model(proxy, body.get("model"))
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        from fastapi import HTTPException
        raise HTTPException(400, "stream must be boolean")
    duration = keep_alive_seconds(body.get("keep_alive"), proxy.idle_seconds)
    runtime, _ = await proxy.acquire(spec)
    payload = {**body, "model": runtime}
    payload.pop("keep_alive", None)
    return await _openai_inference(proxy, spec, path, payload, stream, duration)


@router.post("/v1/chat/completions")
async def openai_chat(request: Request, proxy: ProxyService = Depends(get_proxy)) -> Any:
    return await _openai_request(request, "/v1/chat/completions", proxy)


@router.post("/v1/completions")
async def openai_completions(request: Request, proxy: ProxyService = Depends(get_proxy)) -> Any:
    return await _openai_request(request, "/v1/completions", proxy)


@router.post("/v1/responses")
async def openai_responses(request: Request, proxy: ProxyService = Depends(get_proxy)) -> Any:
    return await _openai_request(request, "/v1/responses", proxy)
