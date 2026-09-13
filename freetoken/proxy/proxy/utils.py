"""Utility functions shared across the proxy."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException


KEEP_ALIVE_RE = re.compile(r"^(-?\d+(?:\.\d+)?)(ns|us|ms|s|m|h)?$")
UNIT_SECONDS = {None: 1, "ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600}


def keep_alive_seconds(value: Any, default: float) -> float | None:
    """Parse keep_alive value to seconds. Returns None for infinite keep-alive."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise HTTPException(400, "invalid keep_alive")
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        match = KEEP_ALIVE_RE.fullmatch(value.strip())
        if not match:
            raise HTTPException(400, "invalid keep_alive")
        seconds = float(match.group(1)) * UNIT_SECONDS[match.group(2)]
    else:
        raise HTTPException(400, "invalid keep_alive")
    return None if seconds < 0 else seconds


def utc_now() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def model_details(spec: Any) -> dict[str, Any]:
    """Generate model details dict from a Model spec."""
    family = spec.name.split("/")[0]
    return {"parent_model": "", "format": "freetoken", "family": family, "families": [family]}


def tag_info(spec: Any, modified_at: str) -> dict[str, Any]:
    """Generate a tag entry for catalog display."""
    return {
        "name": spec.name,
        "model": spec.name,
        "modified_at": modified_at,
        "size": 0,
        "digest": _digest(spec),
        "details": model_details(spec),
    }


def _digest(spec: Any) -> str:
    return hashlib.sha256(spec.model.encode()).hexdigest()


def openai_alias(doc: Any, name: str) -> Any:
    """Replace model name with alias in OpenAI response docs."""
    if isinstance(doc, dict):
        if "model" in doc:
            doc["model"] = name
        if isinstance(doc.get("response"), dict) and "model" in doc["response"]:
            doc["response"]["model"] = name
    return doc


def ollama_tools(calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert tool calls to Ollama format."""
    result = []
    for call in calls or []:
        function = call.get("function") or {}
        args = function.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"raw": args}
        result.append({"function": {"name": function.get("name", ""), "arguments": args}})
    return result


def options_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Map Ollama options to OpenAI parameter names."""
    options = body.get("options") or {}
    if not isinstance(options, dict):
        raise HTTPException(400, "options must be an object")
    mapped: dict[str, Any] = {}
    for source, target in {
        "temperature": "temperature",
        "top_p": "top_p",
        "num_predict": "max_tokens",
        "stop": "stop",
        "seed": "seed",
        "frequency_penalty": "frequency_penalty",
        "presence_penalty": "presence_penalty",
    }.items():
        if source in options:
            mapped[target] = options[source]
    if "format" in body:
        fmt = body["format"]
        mapped["response_format"] = (
            {"type": "json_object"} if fmt == "json"
            else {"type": "json_schema", "json_schema": {"name": "response", "schema": fmt}}
        )
    return mapped


async def sse_json(response: Any) -> Any:
    """Async generator that yields JSON from an SSE response."""
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            raw = "\n".join(data)
            data.clear()
            if raw == "[DONE]":
                return
            yield json.loads(raw)
    if data:
        raw = "\n".join(data)
        if raw != "[DONE]":
            yield json.loads(raw)
