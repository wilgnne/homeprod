"""Proxy service - handles daemon/serve communication and model lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any

import httpx
from fastapi import HTTPException

log = logging.getLogger("freetoken_ollama_proxy")


class ProxyService:
    """Manages the FreeToken daemon lifecycle and proxies requests to serve."""

    def __init__(
        self,
        catalog: dict[str, Any],
        modified_at: str,
        *,
        daemon_url: str,
        serve_url: str,
        token: str = "",
        idle_seconds: float = 300,
        start_timeout_seconds: float = 900,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.catalog = catalog
        self.modified_at = modified_at
        self.daemon_url = daemon_url.rstrip("/")
        self.serve_url = serve_url.rstrip("/")
        self.token = token
        self.idle_seconds = idle_seconds
        self.start_timeout_seconds = start_timeout_seconds
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5, read=None, write=30, pool=5)
        )
        self.owns_client = client is None
        self.lock = asyncio.Lock()
        self.active = 0
        self.deadline: float | None = None
        self.deadline_initialized = False
        self.unload_requested = False
        self.sweeper: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background idle-sweep task."""
        self.sweeper = asyncio.create_task(self._sweep())

    async def close(self) -> None:
        """Stop sweeper and close HTTP client."""
        if self.sweeper:
            self.sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await self.sweeper
        if self.owns_client:
            await self.client.aclose()

    async def _daemon(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a request to the FreeToken daemon."""
        headers = {"X-FT-Token": self.token} if self.token else {}
        try:
            response = await self.client.request(
                method,
                self.daemon_url + path,
                json=body,
                headers=headers,
                timeout=60 if path == "/engine/stop" else 10,
            )
        except httpx.RequestError as exc:
            raise HTTPException(503, f"FreeToken daemon unavailable: {exc}") from exc
        if response.status_code >= 400:
            detail = (
                response.json()
                if "json" in response.headers.get("content-type", "")
                else response.text
            )
            raise HTTPException(
                409 if response.status_code == 409 else 503, detail
            )
        try:
            return response.json()
        except ValueError as exc:
            raise HTTPException(502, "invalid daemon response") from exc

    async def status(self) -> dict[str, Any]:
        """Get engine status from daemon."""
        st = await self._daemon("GET", "/engine/status")
        if st.get("running") and not self.deadline_initialized:
            self.deadline = time.monotonic() + self.idle_seconds
            self.deadline_initialized = True
        if not st.get("running"):
            self.deadline = None
            self.deadline_initialized = False
            self.unload_requested = False
        return st

    async def acquire(self, spec: Any) -> tuple[str, int]:
        """Acquire/initialize the model in the daemon."""
        started = time.monotonic_ns()
        async with self.lock:
            st = await self.status()
            if st.get("running") and st.get("model") != spec.model:
                raise HTTPException(409, f"another model is active: {st.get('model')}")
            if not st.get("running"):
                await self._daemon("POST", "/engine/start", {
                    "model": spec.model,
                    "port": 1919,
                    "args": ["--host", "0.0.0.0", *spec.args],
                })
                self.deadline_initialized = True
                self.deadline = time.monotonic() + self.idle_seconds
            runtime_model = await self._ready_model(spec.model)
            self.active += 1
            self.deadline = None
            return runtime_model, time.monotonic_ns() - started

    async def _ready_model(self, expected: str) -> str:
        """Poll until the model is loaded and ready."""
        until = time.monotonic() + self.start_timeout_seconds
        while True:
            try:
                health = await self.client.get(
                    self.serve_url + "/health", timeout=3
                )
                if health.status_code == 200:
                    report = health.json()
                    if report.get("status") == "error":
                        raise HTTPException(
                            503,
                            f"FreeToken engine failed to load: {report.get('message', 'unknown error')}",
                        )
                    if report.get("status") == "ok" and report.get("maintenance", "serving") == "serving":
                        response = await self.client.get(
                            self.serve_url + "/v1/models", timeout=3
                        )
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
                raise HTTPException(
                    504,
                    "FreeToken model load timed out; daemon may still be loading",
                )
            await asyncio.sleep(min(0.25, max(0, until - time.monotonic())))

    async def release(self, duration: float | None) -> None:
        """Release a model usage slot."""
        async with self.lock:
            self.active = max(0, self.active - 1)
            self.deadline_initialized = True
            if duration == 0:
                self.unload_requested = True
            self.deadline = (
                time.monotonic()
                if self.unload_requested
                else (None if duration is None else time.monotonic() + duration)
            )
            if self.active == 0 and self.unload_requested:
                await self._stop()

    async def unload(self, spec: Any) -> None:
        """Request unload of a model."""
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
        """Stop the engine via daemon."""
        try:
            await self._daemon("POST", "/engine/stop", {"force": False})
            self.deadline = None
            self.deadline_initialized = False
            self.unload_requested = False
        except HTTPException as exc:
            log.error("idle stop failed; engine preserved: %s", exc.detail)
            self.deadline = time.monotonic() + 30

    async def _sweep(self) -> None:
        """Background task to stop the engine when idle."""
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

    async def upstream(
        self,
        path: str,
        payload: dict[str, Any],
        stream: bool,
        *,
        raise_on_error: bool = True,
    ) -> httpx.Response:
        """Forward a request to the serve endpoint."""
        try:
            request = self.client.build_request(
                "POST", self.serve_url + path, json=payload
            )
            response = await self.client.send(request, stream=stream)
        except httpx.RequestError as exc:
            raise HTTPException(502, f"FreeToken serve unavailable: {exc}") from exc
        if raise_on_error and response.status_code >= 400:
            content = await response.aread()
            await response.aclose()
            raise HTTPException(
                502,
                f"FreeToken returned HTTP {response.status_code}: {content.decode(errors='replace')[:500]}",
            )
        return response
