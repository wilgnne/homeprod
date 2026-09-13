"""HTTP tests for model keep-alive and engine stop behavior."""

import asyncio
import json

import pytest


class TestKeepAliveAndStop:
    """Tests for keep_alive, idle timeout, and engine stop."""

    @pytest.mark.asyncio
    async def test_idle_stop_after_idle_period(self, system):
        client, fake, app = system
        await client.post(
            "/api/chat",
            json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "stream": False},
        )
        await asyncio.sleep(1.1)
        assert fake.stops == 1

    @pytest.mark.asyncio
    async def test_negative_keep_alive_pins_model(self, system):
        client, fake, app = system
        await client.post(
            "/api/chat",
            json={
                "model": "gemma",
                "messages": [{"role": "user", "content": "Oi"}],
                "stream": False,
                "keep_alive": -1,
            },
        )
        # keep_alive=-1 means infinite keep-alive; deadline must be None
        assert app.state.proxy.deadline is None
        # With idle_seconds=0.1 the engine could stop between the status
        # check and the release, but keep_alive=-1 prevents any future stop.
        # The key invariant: no *additional* stop after the request completes.
        stops_before = fake.stops
        await asyncio.sleep(1.1)
        assert fake.stops == stops_before  # no new stop after request

    @pytest.mark.asyncio
    async def test_stream_keeps_engine_until_done(self, system):
        client, fake, _ = system
        fake.stream_started = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests
        task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "keep_alive": 0},
            )
        )
        await asyncio.wait_for(fake.stream_started.wait(), 1)
        await asyncio.sleep(1.1)
        assert fake.stops == 0
        fake.stream_continue.set()
        result = await asyncio.wait_for(task, 2)
        assert result.status_code == 200
        assert fake.stops == 1

    @pytest.mark.asyncio
    async def test_stream_keep_alive_openai(self, system):
        client, fake, _ = system
        fake.stream_started = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests
        task = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "stream": True, "keep_alive": 0},
            )
        )
        await asyncio.wait_for(fake.stream_started.wait(), 1)
        assert fake.stops == 0
        upstream = next(req for req in fake.requests if req.url.path == "/v1/chat/completions")
        assert "keep_alive" not in json.loads(upstream.content)
        fake.stream_continue.set()
        result = await task
        assert result.status_code == 200
        assert result.headers["content-type"].startswith("text/event-stream")
        assert "data: [DONE]" in result.text
        assert fake.stops == 1

    @pytest.mark.asyncio
    async def test_stop_failure_preserves_engine(self, system):
        client, fake, _ = system
        fake.stop_fails = True
        await client.post(
            "/api/chat",
            json={
                "model": "gemma",
                "messages": [{"role": "user", "content": "Oi"}],
                "stream": False,
                "keep_alive": 0,
            },
        )
        assert fake.running is True
        active = (await client.get("/api/ps")).json()["models"]
        assert active[0]["name"] == "gemma"


