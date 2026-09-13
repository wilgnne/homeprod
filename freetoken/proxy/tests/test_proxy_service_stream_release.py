"""Cancellation and resource release during upstream streaming."""

import asyncio

import httpx
import pytest

from proxy.main import create_app
from proxy.models import Model
from tests.fake_upstream import FakeUpstream


class TestStreamRelease:
    """Release the active request when an upstream stream ends or fails."""

    # ── P1: cancel while waiting for upstream headers — slot must be released ──

    @pytest.mark.asyncio
    async def test_cancel_upstream_ollama_chat_stream(self, catalog_path):
        """P1: cancel /api/chat while upstream blocks before body sent — active returns to 0."""
        fake = FakeUpstream()
        fake.stream_blocked = asyncio.Event()
        fake.before_stream_body = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests
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
            idle_seconds=300,
            start_timeout_seconds=0.05,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as client:
                proxy = app.state.proxy
                task = asyncio.create_task(
                    client.post(
                        "/api/chat",
                        json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}]},
                    )
                )
                await asyncio.wait_for(fake.stream_blocked.wait(), 1)
                assert proxy.active == 1
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert proxy.active == 0
                await asyncio.sleep(0.2)
        await upstream.aclose()

    @pytest.mark.asyncio
    async def test_cancel_upstream_ollama_generate_stream(self, catalog_path):
        """P1: cancel /api/generate stream=true while upstream blocks before body sent — active returns to 0."""
        fake = FakeUpstream()
        fake.stream_blocked = asyncio.Event()
        fake.before_stream_body = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests
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
            idle_seconds=300,
            start_timeout_seconds=0.05,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as client:
                proxy = app.state.proxy
                task = asyncio.create_task(
                    client.post(
                        "/api/generate",
                        json={"model": "gemma", "prompt": "say hi", "stream": True},
                    )
                )
                await asyncio.wait_for(fake.stream_blocked.wait(), 1)
                assert proxy.active == 1
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert proxy.active == 0
                await asyncio.sleep(0.2)
        await upstream.aclose()

    @pytest.mark.asyncio
    async def test_cancel_upstream_openai_chat_stream(self, catalog_path):
        """P1: cancel /v1/chat/completions stream=true while upstream blocks before body sent — active returns to 0."""
        fake = FakeUpstream()
        fake.stream_blocked = asyncio.Event()
        fake.before_stream_body = asyncio.Event()
        fake.stream_started = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests
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
            idle_seconds=300,
            start_timeout_seconds=0.05,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as client:
                proxy = app.state.proxy
                task = asyncio.create_task(
                    client.post(
                        "/v1/chat/completions",
                        json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "stream": True},
                    )
                )
                await asyncio.wait_for(fake.stream_blocked.wait(), 1)
                assert proxy.active == 1
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert proxy.active == 0
                await asyncio.sleep(0.2)
        await upstream.aclose()

    @pytest.mark.asyncio
    async def test_cancel_upstream_openai_completions(self, catalog_path):
        """P1: cancel /v1/completions while upstream blocks before body sent — active returns to 0."""
        fake = FakeUpstream()
        fake.stream_blocked = asyncio.Event()
        fake.before_stream_body = asyncio.Event()
        fake.stream_started = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests
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
            idle_seconds=300,
            start_timeout_seconds=0.05,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as client:
                proxy = app.state.proxy
                task = asyncio.create_task(
                    client.post(
                        "/v1/completions",
                        json={"model": "gemma", "prompt": "say hi", "stream": True},
                    )
                )
                await asyncio.wait_for(fake.stream_blocked.wait(), 1)
                assert proxy.active == 1
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert proxy.active == 0
                await asyncio.sleep(0.2)
        await upstream.aclose()

    @pytest.mark.asyncio
    async def test_cancel_upstream_openai_responses(self, catalog_path):
        """P1: cancel /v1/responses while upstream blocks before body sent — active returns to 0."""
        fake = FakeUpstream()
        fake.stream_blocked = asyncio.Event()
        fake.before_stream_body = asyncio.Event()
        fake.stream_started = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests
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
            idle_seconds=300,
            start_timeout_seconds=0.05,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as client:
                proxy = app.state.proxy
                task = asyncio.create_task(
                    client.post(
                        "/v1/responses",
                        json={"model": "gemma", "input": "Oi", "stream": True},
                    )
                )
                await asyncio.wait_for(fake.stream_blocked.wait(), 1)
                assert proxy.active == 1
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert proxy.active == 0
                await asyncio.sleep(0.2)
        await upstream.aclose()

    # ── P1 V4: release_once runs even if response.aclose() fails ──

    @pytest.mark.asyncio
    async def test_release_when_aclose_fails(self, catalog_path):
        """When response.aclose() raises, release_once still runs — active returns to 0."""
        fake = FakeUpstream()
        fake.stream_blocked = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests

        class FailingAcloseTransport:
            """Mock transport that makes upstream response.aclose() raise."""
            def __init__(self, wrapped):
                self.wrapped = wrapped

            async def handle_async_request(self, request):
                response = await self.wrapped.handle_async_request(request)
                orig_aclose = response.aclose

                async def failing_aclose():
                    try:
                        await orig_aclose()
                    except Exception:
                        pass
                    raise RuntimeError("aclose failure")

                response.aclose = failing_aclose
                return response

            async def aclose(self):
                await self.wrapped.aclose()

        wrapped_transport = httpx.MockTransport(fake.handle)
        transport = FailingAcloseTransport(wrapped_transport)
        client = httpx.AsyncClient(
            transport=transport,
            base_url="http://daemon",
        )
        app = create_app(
            {"gemma": Model("gemma", "google/gemma-4-E2B")},
            "2026-01-01T00:00:00Z",
            daemon_url="http://daemon",
            serve_url="http://serve",
            client=client,
            idle_seconds=300,
            start_timeout_seconds=0.05,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as client_proxy:
                proxy = app.state.proxy
                task = asyncio.create_task(
                    client_proxy.post(
                        "/api/chat",
                        json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}]},
                    )
                )
                await asyncio.wait_for(fake.stream_blocked.wait(), 1)
                assert proxy.active == 1
                # aclose will fail — expect RuntimeError to propagate
                err = None
                try:
                    await task
                except RuntimeError as e:
                    err = e
                assert err is not None and "aclose failure" in str(err)
                # P1 V4: even though aclose() raised, release_once ran
                assert proxy.active == 0
        await client.aclose()

    @pytest.mark.asyncio
    async def test_release_after_normal_stream_completes(self, catalog_path):
        """Even without aclose failure, release_once runs on normal completion."""
        fake = FakeUpstream()
        fake.stream_blocked = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests
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
            idle_seconds=300,
            start_timeout_seconds=0.05,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://proxy"
            ) as client:
                proxy = app.state.proxy
                fake.stream_continue.set()
                result = await client.post(
                    "/api/chat",
                    json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}]},
                )
                assert result.status_code == 200
                await asyncio.sleep(0.2)
                assert proxy.active == 0
        await upstream.aclose()
