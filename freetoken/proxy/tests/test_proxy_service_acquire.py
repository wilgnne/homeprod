"""Cancellation and resource-release tests for active proxy requests."""

import asyncio

import httpx
import pytest

from proxy.main import create_app
from proxy.models import Model
from tests.fake_upstream import FakeUpstream


class TestAcquireCancellation:
    """Cancellation while waiting to acquire an active model."""

    # ── P0: cancel while waiting on acquire() lock with another active stream ──

    @pytest.mark.asyncio
    async def test_cancel_acquire_with_active_stream_ollama_chat(self, catalog_path):
        """P2: second request cancelled while waiting on acquire() lock — first stream completes normally."""
        fake = FakeUpstream()
        fake.ready = True
        fake.stream_started = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.acquire_entered = asyncio.Event()
        fake.stream_blocked = asyncio.Event()
        fake.before_stream_body = asyncio.Event()
        upstream = httpx.AsyncClient(
            transport=httpx.MockTransport(fake.handle),
            base_url="http://daemon",
        )
        app = create_app(
            {"gemma": Model("gemma", "google/gemma-4-E2B")},
            "2026-01-01T00:00:00Z",
            daemon_url="http://daemon",
            serve_url="http://serve",
            client=upstream,
            idle_seconds=0,
            start_timeout_seconds=5,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as client:
                proxy = app.state.proxy
                # First request: completes normally (acquire → stream → release)
                first = asyncio.create_task(
                    client.post(
                        "/api/chat",
                        json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}]},
                    )
                )
                await asyncio.sleep(0.05)
                assert fake.running is True
                # Second request: waits for the acquire lock (first still holds it during _ready_model)
                second = asyncio.create_task(
                    client.post(
                        "/api/chat",
                        json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}]},
                    )
                )
                # Give second time to block on lock
                await asyncio.sleep(0.05)
                # Release first and second from before_stream_body
                fake.before_stream_body.set()
                # Second is blocked waiting for lock — cancel it
                second.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await second
                # First should complete normally (it already incremented active, second never did)
                fake.stream_continue.set()
                result = await asyncio.wait_for(first, 2)
                assert result.status_code == 200
                assert proxy.active == 0
                assert fake.stops == 1

    @pytest.mark.asyncio
    async def test_cancel_acquire_with_active_stream_ollama_generate(self, catalog_path):
        """P2: second request cancelled while waiting on acquire() lock — first stream completes normally."""
        fake = FakeUpstream()
        fake.ready = True
        fake.stream_started = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.acquire_entered = asyncio.Event()
        fake.stream_blocked = asyncio.Event()
        fake.before_stream_body = asyncio.Event()
        upstream = httpx.AsyncClient(
            transport=httpx.MockTransport(fake.handle),
            base_url="http://daemon",
        )
        app = create_app(
            {"gemma": Model("gemma", "google/gemma-4-E2B")},
            "2026-01-01T00:00:00Z",
            daemon_url="http://daemon",
            serve_url="http://serve",
            client=upstream,
            idle_seconds=0,
            start_timeout_seconds=5,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as client:
                proxy = app.state.proxy
                first = asyncio.create_task(
                    client.post(
                        "/api/generate",
                        json={"model": "gemma", "prompt": "say hi", "stream": True},
                    )
                )
                await asyncio.sleep(0.05)
                second = asyncio.create_task(
                    client.post(
                        "/api/generate",
                        json={"model": "gemma", "prompt": "again", "stream": True},
                    )
                )
                await asyncio.sleep(0.05)
                fake.before_stream_body.set()
                second.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await second
                fake.stream_continue.set()
                result = await asyncio.wait_for(first, 2)
                assert result.status_code == 200
                assert proxy.active == 0
                assert fake.stops == 1
        await upstream.aclose()

    @pytest.mark.asyncio
    async def test_cancel_acquire_with_active_stream_openai(self, catalog_path):
        """P2: second request cancelled while waiting on acquire() lock — first stream completes normally."""
        fake = FakeUpstream()
        fake.ready = True
        fake.stream_started = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.acquire_entered = asyncio.Event()
        fake.stream_blocked = asyncio.Event()
        fake.before_stream_body = asyncio.Event()
        upstream = httpx.AsyncClient(
            transport=httpx.MockTransport(fake.handle),
            base_url="http://daemon",
        )
        app = create_app(
            {"gemma": Model("gemma", "google/gemma-4-E2B")},
            "2026-01-01T00:00:00Z",
            daemon_url="http://daemon",
            serve_url="http://serve",
            client=upstream,
            idle_seconds=0,
            start_timeout_seconds=5,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as client:
                proxy = app.state.proxy
                first = asyncio.create_task(
                    client.post(
                        "/v1/chat/completions",
                        json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "stream": True},
                    )
                )
                await asyncio.sleep(0.05)
                second = asyncio.create_task(
                    client.post(
                        "/v1/chat/completions",
                        json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}]},
                    )
                )
                await asyncio.sleep(0.05)
                fake.before_stream_body.set()
                second.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await second
                fake.stream_continue.set()
                result = await asyncio.wait_for(first, 2)
                assert result.status_code == 200
                assert proxy.active == 0
                assert fake.stops == 1
        await upstream.aclose()

