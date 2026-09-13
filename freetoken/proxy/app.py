"""Small Ollama-shaped HTTP facade for a single FreeToken daemon/serve pair."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

log = logging.getLogger("freetoken_ollama_proxy")
VERSION = "0.1.0"
KEEP_ALIVE_RE = re.compile(r"^(-?\d+(?:\.\d+)?)(ns|us|ms|s|m|h)?$")
UNIT_SECONDS = {None: 1, "ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600}


@dataclass(frozen=True)
class Model:
    name: str
    model: str
    args: tuple[str, ...] = ()


def read_catalog(path: str) -> tuple[dict[str, Model], str]:
    file = Path(path)
    raw = json.loads(file.read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        raise ValueError("catalog must contain a models array")
    models: dict[str, Model] = {}
    for item in raw["models"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("model"), str):
            raise ValueError("each model needs string name and model fields")
        name, model, args = item["name"].strip(), item["model"].strip(), item.get("args", [])
        if not name or not model or name in models or not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise ValueError(f"invalid or duplicate model entry: {name!r}")
        if any(x in {"--host", "--port", "--model", "--model-path"} or x.startswith(("--host=", "--port=", "--model=")) for x in args):
            raise ValueError(f"model {name!r} cannot override host, port or model")
        models[name] = Model(name, model, tuple(args))
    modified_at = datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
    return models, modified_at


def keep_alive_seconds(value: Any, default: float) -> float | None:
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
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def model_details(spec: Model) -> dict[str, Any]:
    return {"parent_model": "", "format": "freetoken", "family": spec.name.split("/")[0], "families": [spec.name.split("/")[0]]}


def tag(spec: Model, modified_at: str) -> dict[str, Any]:
    return {
        "name": spec.name,
        "model": spec.name,
        "modified_at": modified_at,
        "size": 0,  # Catalog entries may be remote Hugging Face IDs; disk size is unknown.
        "digest": hashlib.sha256(spec.model.encode()).hexdigest(),
        "details": model_details(spec),
    }


def ollama_tools(calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
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
    options = body.get("options") or {}
    if not isinstance(options, dict):
        raise HTTPException(400, "options must be an object")
    mapped: dict[str, Any] = {}
    for source, target in {
        "temperature": "temperature", "top_p": "top_p", "num_predict": "max_tokens",
        "stop": "stop", "seed": "seed", "frequency_penalty": "frequency_penalty",
        "presence_penalty": "presence_penalty",
    }.items():
        if source in options:
            mapped[target] = options[source]
    if "format" in body:
        fmt = body["format"]
        mapped["response_format"] = {"type": "json_object"} if fmt == "json" else {"type": "json_schema", "json_schema": {"name": "response", "schema": fmt}}
    return mapped


async def sse_json(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
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


class Proxy:
    def __init__(
        self, catalog: dict[str, Model], modified_at: str, *, daemon_url: str,
        serve_url: str, token: str = "", idle_seconds: float = 300,
        start_timeout_seconds: float = 900, client: httpx.AsyncClient | None = None,
    ) -> None:
        self.catalog = catalog
        self.modified_at = modified_at
        self.daemon_url = daemon_url.rstrip("/")
        self.serve_url = serve_url.rstrip("/")
        self.token = token
        self.idle_seconds = idle_seconds
        self.start_timeout_seconds = start_timeout_seconds
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=None, write=30, pool=5))
        self.owns_client = client is None
        self.lock = asyncio.Lock()
        self.active = 0
        self.deadline: float | None = None
        self.deadline_initialized = False
        self.unload_requested = False
        self.sweeper: asyncio.Task | None = None

    async def start(self) -> None:
        self.sweeper = asyncio.create_task(self._sweep())

    async def close(self) -> None:
        if self.sweeper:
            self.sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await self.sweeper
        if self.owns_client:
            await self.client.aclose()

    async def _daemon(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"X-FT-Token": self.token} if self.token else {}
        try:
            response = await self.client.request(
                method, self.daemon_url + path, json=body, headers=headers,
                timeout=60 if path == "/engine/stop" else 10,
            )
        except httpx.RequestError as exc:
            raise HTTPException(503, f"FreeToken daemon unavailable: {exc}") from exc
        if response.status_code >= 400:
            detail = response.json() if "json" in response.headers.get("content-type", "") else response.text
            raise HTTPException(409 if response.status_code == 409 else 503, detail)
        try:
            return response.json()
        except ValueError as exc:
            raise HTTPException(502, "invalid daemon response") from exc

    async def status(self) -> dict[str, Any]:
        st = await self._daemon("GET", "/engine/status")
        if st.get("running") and not self.deadline_initialized:
            self.deadline = time.monotonic() + self.idle_seconds
            self.deadline_initialized = True
        if not st.get("running"):
            self.deadline = None
            self.deadline_initialized = False
            self.unload_requested = False
        return st

    async def acquire(self, spec: Model) -> tuple[str, int]:
        started = time.monotonic_ns()
        async with self.lock:
            st = await self.status()
            if st.get("running") and st.get("model") != spec.model:
                raise HTTPException(409, f"another model is active: {st.get('model')}")
            if not st.get("running"):
                await self._daemon("POST", "/engine/start", {
                    "model": spec.model, "port": 1919, "args": ["--host", "0.0.0.0", *spec.args],
                })
                self.deadline_initialized = True
                self.deadline = time.monotonic() + self.idle_seconds
            runtime_model = await self._ready_model(spec.model)
            self.active += 1
            self.deadline = None
            return runtime_model, time.monotonic_ns() - started

    async def _ready_model(self, expected: str) -> str:
        until = time.monotonic() + self.start_timeout_seconds
        while True:
            try:
                health = await self.client.get(self.serve_url + "/health", timeout=3)
                if health.status_code == 200:
                    report = health.json()
                    if report.get("status") == "error":
                        raise HTTPException(503, f"FreeToken engine failed to load: {report.get('message', 'unknown error')}")
                    if report.get("status") == "ok" and report.get("maintenance", "serving") == "serving":
                        response = await self.client.get(self.serve_url + "/v1/models", timeout=3)
                        if response.status_code == 200:
                            data = response.json().get("data") or []
                            if data and isinstance(data[0].get("id"), str):
                                return data[0]["id"]
            except (httpx.RequestError, ValueError, AttributeError, TypeError):
                pass
            st = await self.status()
            if not st.get("running") or st.get("model") != expected:
                raise HTTPException(503, "FreeToken engine exited while loading")
            if time.monotonic() >= until:
                raise HTTPException(504, "FreeToken model load timed out; daemon may still be loading")
            await asyncio.sleep(min(0.25, max(0, until - time.monotonic())))

    async def release(self, duration: float | None) -> None:
        async with self.lock:
            self.active = max(0, self.active - 1)
            self.deadline_initialized = True
            if duration == 0:
                self.unload_requested = True
            self.deadline = time.monotonic() if self.unload_requested else (None if duration is None else time.monotonic() + duration)
            if self.active == 0 and self.unload_requested:
                await self._stop()

    async def unload(self, spec: Model) -> None:
        async with self.lock:
            st = await self.status()
            if not st.get("running"):
                return
            if st.get("model") != spec.model:
                raise HTTPException(409, f"another model is active: {st.get('model')}")
            self.deadline_initialized = True
            self.unload_requested = True
            self.deadline = time.monotonic()
            if self.active == 0:
                await self._stop()

    async def _stop(self) -> None:
        try:
            await self._daemon("POST", "/engine/stop", {"force": False})
            self.deadline = None
            self.deadline_initialized = False
            self.unload_requested = False
        except HTTPException as exc:
            log.error("idle stop failed; engine preserved: %s", exc.detail)
            self.deadline = time.monotonic() + 30

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(1)
            async with self.lock:
                if not self.deadline_initialized:
                    try:
                        await self.status()
                    except HTTPException:
                        continue
                if self.active or self.deadline is None or time.monotonic() < self.deadline:
                    continue
                await self._stop()

    async def upstream(self, path: str, payload: dict[str, Any], stream: bool) -> httpx.Response:
        try:
            request = self.client.build_request("POST", self.serve_url + path, json=payload)
            response = await self.client.send(request, stream=stream)
        except httpx.RequestError as exc:
            raise HTTPException(502, f"FreeToken serve unavailable: {exc}") from exc
        if response.status_code >= 400:
            content = await response.aread()
            await response.aclose()
            raise HTTPException(502, f"FreeToken returned HTTP {response.status_code}: {content.decode(errors='replace')[:500]}")
        return response


def create_app(
    catalog: dict[str, Model] | None = None, modified_at: str | None = None,
    *, daemon_url: str | None = None, serve_url: str | None = None,
    token: str | None = None, idle_seconds: float | None = None,
    start_timeout_seconds: float | None = None, client: httpx.AsyncClient | None = None,
) -> FastAPI:
    if catalog is None:
        catalog, loaded_modified_at = read_catalog(os.getenv("FT_MODEL_CATALOG", "/app/models.json"))
        modified_at = modified_at or loaded_modified_at
    proxy = Proxy(
        catalog, modified_at or utc_now(),
        daemon_url=daemon_url or os.getenv("FT_DAEMON_URL", "http://daemon:19000"),
        serve_url=serve_url or os.getenv("FT_SERVE_URL", "http://daemon:1919"),
        token=token if token is not None else os.getenv("FT_DAEMON_TOKEN", ""),
        idle_seconds=idle_seconds if idle_seconds is not None else float(os.getenv("FT_IDLE_SECONDS", "300")),
        start_timeout_seconds=start_timeout_seconds if start_timeout_seconds is not None else float(os.getenv("FT_START_TIMEOUT_SECONDS", "900")),
        client=client,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await proxy.start()
        try:
            yield
        finally:
            await proxy.close()

    app = FastAPI(title="FreeToken Ollama proxy", version=VERSION, lifespan=lifespan)
    app.state.proxy = proxy

    def find_model(body: dict[str, Any]) -> Model:
        name = body.get("model") or body.get("name")
        if not isinstance(name, str) or name not in proxy.catalog:
            raise HTTPException(404, f"model not found: {name}")
        return proxy.catalog[name]

    async def json_body(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "invalid JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be an object")
        return body

    @app.get("/")
    async def root():
        return "Ollama is running"

    @app.get("/api/version")
    async def version():
        return {"version": VERSION}

    @app.get("/api/tags")
    async def tags():
        return {"models": [tag(spec, proxy.modified_at) for spec in proxy.catalog.values()]}

    @app.post("/api/show")
    async def show(request: Request):
        spec = find_model(await json_body(request))
        return {"details": model_details(spec), "model_info": {}, "capabilities": ["completion"], "modified_at": proxy.modified_at}

    @app.get("/api/ps")
    async def ps():
        # Loading holds the lifecycle lock; polling loaded models must not wait for it.
        st = await proxy._daemon("GET", "/engine/status")
        if not st.get("running"):
            return {"models": []}
        spec = next((s for s in proxy.catalog.values() if s.model == st.get("model")), None)
        if spec is None:
            return {"models": []}
        try:
            health = await proxy.client.get(proxy.serve_url + "/health", timeout=3)
            report = health.json() if health.status_code == 200 else {}
        except (httpx.RequestError, ValueError, TypeError):
            return {"models": []}
        if not isinstance(report, dict):
            return {"models": []}
        if report.get("status") != "ok" or report.get("maintenance", "serving") != "serving":
            return {"models": []}

        if proxy.active or not proxy.deadline_initialized:
            remaining = proxy.idle_seconds
        elif proxy.deadline is None:
            # Ollama clients, including Open WebUI, expect a parseable timestamp.
            expires_at = "9999-12-31T23:59:59Z"
            remaining = None
        else:
            remaining = max(0, proxy.deadline - time.monotonic())
        if remaining is not None:
            expires_at = datetime.fromtimestamp(time.time() + remaining, timezone.utc).isoformat().replace("+00:00", "Z")
        entry = tag(spec, proxy.modified_at)
        entry["expires_at"] = expires_at
        entry["size_vram"] = 0
        return {"models": [entry]}

    @app.post("/api/chat")
    async def chat(request: Request):
        body = await json_body(request)
        spec = find_model(body)
        messages = body.get("messages")
        if messages is None:
            messages = []
        if not isinstance(messages, list):
            raise HTTPException(400, "messages must be an array")
        duration = keep_alive_seconds(body.get("keep_alive"), proxy.idle_seconds)
        stream = body.get("stream", True)
        if not isinstance(stream, bool):
            raise HTTPException(400, "stream must be boolean")
        options = options_to_openai(body)
        if not messages and duration == 0:
            await proxy.unload(spec)
            return {"model": spec.name, "created_at": utc_now(), "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "unload"}
        runtime, load_ns = await proxy.acquire(spec)
        if not messages:
            await proxy.release(duration)
            return {"model": spec.name, "created_at": utc_now(), "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "load", "load_duration": load_ns}
        payload = {"model": runtime, "messages": messages, "stream": stream, **options}
        if "tools" in body:
            payload["tools"] = body["tools"]
        return await inference(proxy, spec, "/v1/chat/completions", payload, stream, duration, load_ns, chat_mode=True)

    @app.post("/api/generate")
    async def generate(request: Request):
        body = await json_body(request)
        spec = find_model(body)
        duration = keep_alive_seconds(body.get("keep_alive"), proxy.idle_seconds)
        stream = body.get("stream", True)
        if not isinstance(stream, bool):
            raise HTTPException(400, "stream must be boolean")
        prompt = body.get("prompt", "")
        if not isinstance(prompt, str):
            raise HTTPException(400, "prompt must be a string")
        if body.get("images"):
            raise HTTPException(400, "images are not supported by /api/generate")
        options = options_to_openai(body)
        if not prompt and duration == 0:
            await proxy.unload(spec)
            return {"model": spec.name, "created_at": utc_now(), "response": "", "done": True, "done_reason": "unload"}
        runtime, load_ns = await proxy.acquire(spec)
        if not prompt:
            await proxy.release(duration)
            return {"model": spec.name, "created_at": utc_now(), "response": "", "done": True, "done_reason": "load", "load_duration": load_ns}
        if isinstance(body.get("system"), str) and body["system"]:
            prompt = body["system"] + "\n\n" + prompt
        payload = {"model": runtime, "prompt": prompt, "stream": stream, **options}
        if "suffix" in body:
            payload["suffix"] = body["suffix"]
        return await inference(proxy, spec, "/v1/completions", payload, stream, duration, load_ns, chat_mode=False)

    return app


async def inference(
    proxy: Proxy, spec: Model, path: str, payload: dict[str, Any], stream: bool,
    duration: float | None, load_ns: int, *, chat_mode: bool,
):
    began = time.monotonic_ns()
    try:
        response = await proxy.upstream(path, payload, stream)
    except Exception:
        await proxy.release(duration)
        raise
    if not stream:
        try:
            doc = response.json()
            choice = (doc.get("choices") or [{}])[0]
            content = choice.get("message", {}) if chat_mode else choice
            result: dict[str, Any] = {"model": spec.name, "created_at": utc_now(), "done": True, "done_reason": choice.get("finish_reason") or "stop", "total_duration": time.monotonic_ns() - began, "load_duration": load_ns}
            if chat_mode:
                message = {"role": "assistant", "content": content.get("content") or ""}
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
            await response.aclose()
            await proxy.release(duration)

    released = False

    async def cleanup() -> None:
        nonlocal released
        if not released:
            released = True
            await response.aclose()
            await proxy.release(duration)

    async def chunks() -> AsyncIterator[bytes]:
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
                        saved = calls.setdefault(index, {"function": {"name": "", "arguments": ""}})
                        function = call.get("function") or {}
                        saved["function"]["name"] += function.get("name") or ""
                        saved["function"]["arguments"] += function.get("arguments") or ""
                    text = delta.get("content") or (choice.get("text") if not chat_mode else "") or ""
                    thinking = delta.get("reasoning_content") or ""
                    if text or thinking:
                        item: dict[str, Any] = {"model": spec.name, "created_at": utc_now(), "done": False}
                        if chat_mode:
                            item["message"] = {"role": "assistant", "content": text}
                            if thinking:
                                item["message"]["thinking"] = thinking
                        else:
                            item["response"] = text
                        yield (json.dumps(item) + "\n").encode()
            if calls and chat_mode:
                item = {"model": spec.name, "created_at": utc_now(), "done": False, "message": {"role": "assistant", "content": "", "tool_calls": ollama_tools([calls[i] for i in sorted(calls)])}}
                yield (json.dumps(item) + "\n").encode()
            final: dict[str, Any] = {"model": spec.name, "created_at": utc_now(), "done": True, "done_reason": finish, "total_duration": time.monotonic_ns() - began, "load_duration": load_ns, "prompt_eval_count": usage.get("prompt_tokens", 0), "eval_count": usage.get("completion_tokens", 0)}
            final["message"] = {"role": "assistant", "content": ""} if chat_mode else None
            if not chat_mode:
                final.pop("message")
                final["response"] = ""
            yield (json.dumps(final) + "\n").encode()
        except Exception as exc:
            log.exception("upstream stream failed")
            yield (json.dumps({"error": f"FreeToken stream failed: {exc}"}) + "\n").encode()
        finally:
            await cleanup()

    return StreamingResponse(chunks(), media_type="application/x-ndjson", background=BackgroundTask(cleanup))


if os.getenv("FT_MODEL_CATALOG") or Path("/app/models.json").exists():
    app = create_app()
else:
    # Allows importing the module for tests and local development without a mounted catalog.
    app = create_app({}, utc_now())
