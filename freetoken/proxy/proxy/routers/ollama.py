"""Ollama API routes - chat and generate endpoints."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response, StreamingResponse

from proxy.dependencies import find_model, json_body, get_proxy
from proxy.schemas import ChatRequest, GenerateRequest, ShowRequest
from proxy.proxy_service import ProxyService
from proxy.utils import (
    keep_alive_seconds,
    ollama_tools,
    options_to_openai,
    sse_json,
    utc_now,
)

router = APIRouter()


@router.post("/api/chat")
async def chat(request: Request, proxy: ProxyService = Depends(get_proxy)) -> Any:
    body = await json_body(request)
    spec = find_model(proxy, body.get("model"))
    messages = body.get("messages")
    if messages is None:
        messages = []
    if not isinstance(messages, list):
        return Response(
            content='{"error": "messages must be an array"}',
            status_code=400,
            media_type="application/json",
        )
    duration = keep_alive_seconds(body.get("keep_alive"), proxy.idle_seconds)
    stream = body.get("stream", True)
    if not isinstance(stream, bool):
        return Response(
            content='{"error": "stream must be boolean"}',
            status_code=400,
            media_type="application/json",
        )
    options = options_to_openai(body)
    if not messages and duration == 0:
        await proxy.unload(spec)
        return {
            "model": spec.name,
            "created_at": utc_now(),
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "unload",
        }
    runtime, load_ns = await proxy.acquire(spec)
    if not messages:
        await proxy.release(duration)
        return {
            "model": spec.name,
            "created_at": utc_now(),
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "load",
            "load_duration": load_ns,
        }
    payload = {
        "model": runtime,
        "messages": messages,
        "stream": stream,
        **options,
    }
    if "tools" in body:
        payload["tools"] = body["tools"]
    return await _inference(
        proxy, spec, "/v1/chat/completions", payload, stream, duration, load_ns,
        chat_mode=True,
    )


@router.post("/api/generate")
async def generate(request: Request, proxy: ProxyService = Depends(get_proxy)) -> Any:
    body = await json_body(request)
    spec = find_model(proxy, body.get("model"))
    duration = keep_alive_seconds(body.get("keep_alive"), proxy.idle_seconds)
    stream = body.get("stream", True)
    if not isinstance(stream, bool):
        return Response(
            content='{"error": "stream must be boolean"}',
            status_code=400,
            media_type="application/json",
        )
    prompt = body.get("prompt", "")
    if not isinstance(prompt, str):
        return Response(
            content='{"error": "prompt must be a string"}',
            status_code=400,
            media_type="application/json",
        )
    if body.get("images"):
        return Response(
            content='{"error": "images are not supported by /api/generate"}',
            status_code=400,
            media_type="application/json",
        )
    options = options_to_openai(body)
    if not prompt and duration == 0:
        await proxy.unload(spec)
        return {
            "model": spec.name,
            "created_at": utc_now(),
            "response": "",
            "done": True,
            "done_reason": "unload",
        }
    runtime, load_ns = await proxy.acquire(spec)
    if not prompt:
        await proxy.release(duration)
        return {
            "model": spec.name,
            "created_at": utc_now(),
            "response": "",
            "done": True,
            "done_reason": "load",
            "load_duration": load_ns,
        }
    if isinstance(body.get("system"), str) and body["system"]:
        prompt = body["system"] + "\n\n" + prompt
    payload = {
        "model": runtime,
        "prompt": prompt,
        "stream": stream,
        **options,
    }
    if "suffix" in body:
        payload["suffix"] = body["suffix"]
    return await _inference(
        proxy, spec, "/v1/completions", payload, stream, duration, load_ns,
        chat_mode=False,
    )


async def _inference(
    proxy: ProxyService,
    spec: Any,
    path: str,
    payload: dict[str, Any],
    stream: bool,
    duration: float | None,
    load_ns: int,
    *,
    chat_mode: bool,
) -> Any:
    """Execute an inference request and format the response."""
    began = time.monotonic_ns()
    released: list[bool] = [False]

    async def release_once() -> None:
        if released[0]:
            return
        released[0] = True
        await proxy.release(duration)

    try:
        response = await proxy.upstream(path, payload, stream)
    except asyncio.CancelledError:
        await release_once()
        raise
    except Exception:
        await release_once()
        raise

    if not stream:
        try:
            doc = response.json()
            choice = (doc.get("choices") or [{}])[0]
            content = choice.get("message", {}) if chat_mode else choice
            result: dict[str, Any] = {
                "model": spec.name,
                "created_at": utc_now(),
                "done": True,
                "done_reason": choice.get("finish_reason") or "stop",
                "total_duration": time.monotonic_ns() - began,
                "load_duration": load_ns,
            }
            if chat_mode:
                message = {
                    "role": "assistant",
                    "content": content.get("content") or "",
                }
                if content.get("reasoning_content"):
                    message["thinking"] = content["reasoning_content"]
                if content.get("tool_calls"):
                    message["tool_calls"] = ollama_tools(content["tool_calls"])
                result["message"] = message
            else:
                result["response"] = content.get("text") or ""
            usage = doc.get("usage") or {}
            result["prompt_eval_count"] = usage.get("prompt_tokens", 0)
            result["eval_count"] = usage.get("completion_tokens", 0)
            return result
        finally:
            try:
                await response.aclose()
            finally:
                await release_once()

    async def chunks() -> Any:
        usage: dict[str, Any] = {}
        finish = "stop"
        calls: dict[int, dict[str, Any]] = {}
        try:
            async for doc in sse_json(response):
                if "error" in doc:
                    yield (json.dumps({"error": doc["error"]}) + "\n").encode()
                    return
                if doc.get("usage"):
                    usage = doc["usage"]
                for choice in doc.get("choices") or []:
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    for call in delta.get("tool_calls") or []:
                        index = call.get("index", 0)
                        saved = calls.setdefault(
                            index, {"function": {"name": "", "arguments": ""}}
                        )
                        function = call.get("function") or {}
                        saved["function"]["name"] += function.get("name") or ""
                        saved["function"]["arguments"] += function.get("arguments") or ""
                    text = (
                        delta.get("content")
                        or (choice.get("text") if not chat_mode else "")
                        or ""
                    )
                    thinking = delta.get("reasoning_content") or ""
                    if text or thinking:
                        item: dict[str, Any] = {
                            "model": spec.name,
                            "created_at": utc_now(),
                            "done": False,
                        }
                        if chat_mode:
                            item["message"] = {
                                "role": "assistant",
                                "content": text,
                            }
                            if thinking:
                                item["message"]["thinking"] = thinking
                        else:
                            item["response"] = text
                        yield (json.dumps(item) + "\n").encode()
            if calls and chat_mode:
                item = {
                    "model": spec.name,
                    "created_at": utc_now(),
                    "done": False,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": ollama_tools(
                            [calls[i] for i in sorted(calls)]
                        ),
                    },
                }
                yield (json.dumps(item) + "\n").encode()
            final: dict[str, Any] = {
                "model": spec.name,
                "created_at": utc_now(),
                "done": True,
                "done_reason": finish,
                "total_duration": time.monotonic_ns() - began,
                "load_duration": load_ns,
                "prompt_eval_count": usage.get("prompt_tokens", 0),
                "eval_count": usage.get("completion_tokens", 0),
            }
            final["message"] = (
                {"role": "assistant", "content": ""} if chat_mode else None
            )
            if not chat_mode:
                final.pop("message")
                final["response"] = ""
            yield (json.dumps(final) + "\n").encode()
        except Exception as exc:
            yield (
                json.dumps(
                    {"error": f"FreeToken stream failed: {exc}"}
                )
                + "\n"
            ).encode()
        finally:
            try:
                await response.aclose()
            finally:
                await release_once()

    return StreamingResponse(
        chunks(),
        media_type="application/x-ndjson",
    )
