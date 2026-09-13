import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio

from app import Model, create_app


class GatedStream(httpx.AsyncByteStream):
    def __init__(self, fake):
        self.fake = fake

    async def __aiter__(self):
        self.fake.stream_started.set()
        yield b'data: {"choices":[{"delta":{"content":"Oi"}}]}\n\n'
        await self.fake.stream_continue.wait()
        yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


class FakeUpstream:
    def __init__(self):
        self.running = False
        self.model = None
        self.starts = 0
        self.stops = 0
        self.ready = True
        self.daemon_down = False
        self.stop_fails = False
        self.requests = []
        self.stream_started = None
        self.stream_continue = None

    async def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "daemon":
            if self.daemon_down:
                raise httpx.ConnectError("daemon down")
            if path == "/engine/status":
                return httpx.Response(200, json={"running": self.running, "model": self.model, "port": 1919})
            if path == "/engine/start":
                body = json.loads(request.content)
                self.starts += 1
                self.running = True
                self.model = body["model"]
                return httpx.Response(200, json={"pid": 123})
            if path == "/engine/stop":
                self.stops += 1
                if self.stop_fails:
                    return httpx.Response(503, json={"error": "accounting failed", "enginePreserved": True})
                self.running = False
                return httpx.Response(200, json={"already": False})
        if path == "/v1/models":
            # FreeToken exposes this route even while the backend is loading.
            return httpx.Response(200, json={"data": [{"id": "gemma-runtime"}]})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok" if self.ready else "loading", "maintenance": "serving" if self.ready else "loading"})
        if path == "/v1/chat/completions":
            if json.loads(request.content)["stream"]:
                if self.stream_continue is not None:
                    return httpx.Response(200, stream=GatedStream(self), headers={"content-type": "text/event-stream"})
                data = 'data: {"choices":[{"delta":{"content":"Oi"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1}}\n\ndata: [DONE]\n\n'
                return httpx.Response(200, content=data, headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "Oi"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}})
        if path == "/v1/completions":
            if json.loads(request.content)["stream"]:
                data = 'data: {"choices":[{"text":"Olá","finish_reason":null}]}\n\ndata: [DONE]\n\n'
                return httpx.Response(200, content=data, headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={"choices": [{"text": "Olá", "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}})
        return httpx.Response(404)


@pytest_asyncio.fixture
async def system():
    fake = FakeUpstream()
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    app = create_app({"gemma": Model("gemma", "google/gemma-4-E2B"), "other": Model("other", "other/model")}, "2026-01-01T00:00:00Z", daemon_url="http://daemon", serve_url="http://serve", client=upstream, idle_seconds=0.1, start_timeout_seconds=0.05)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
            yield client, fake, app
    await upstream.aclose()


@pytest.mark.asyncio
async def test_catalog_without_running_engine(system):
    client, fake, _ = system
    response = await client.get("/api/tags")
    assert response.status_code == 200
    assert [m["name"] for m in response.json()["models"]] == ["gemma", "other"]
    assert response.json()["models"][0]["details"]["format"] == "freetoken"
    assert fake.starts == 0
    assert (await client.get("/api/ps")).json() == {"models": []}
    assert (await client.post("/api/show", json={"model": "gemma"})).json()["capabilities"] == ["completion"]


@pytest.mark.asyncio
async def test_chat_start_stream_and_busy_model(system):
    client, fake, _ = system
    result = await client.post("/api/chat", json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}]})
    assert result.status_code == 200
    lines = [json.loads(line) for line in result.text.splitlines()]
    assert lines[0]["message"]["content"] == "Oi"
    assert lines[-1]["done"] is True
    assert lines[-1]["eval_count"] == 1
    assert fake.starts == 1
    assert (await client.get("/api/ps")).json()["models"][0]["name"] == "gemma"
    busy = await client.post("/api/chat", json={"model": "other", "messages": [{"role": "user", "content": "Oi"}], "stream": False})
    assert busy.status_code == 409


@pytest.mark.asyncio
async def test_concurrent_start_is_single_flight(system):
    client, fake, _ = system
    body = {"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "stream": False}
    first, second = await asyncio.gather(client.post("/api/chat", json=body), client.post("/api/chat", json=body))
    assert first.status_code == second.status_code == 200
    assert first.json()["message"]["content"] == "Oi"
    assert fake.starts == 1


@pytest.mark.asyncio
async def test_first_chat_waits_for_real_readiness(system):
    client, fake, app = system
    fake.ready = False
    app.state.proxy.start_timeout_seconds = 2
    body = {"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "stream": False}
    task = asyncio.create_task(client.post("/api/chat", json=body))
    await asyncio.sleep(0.05)
    assert fake.starts == 1
    assert not any(req.url.path == "/v1/chat/completions" for req in fake.requests)
    assert (await asyncio.wait_for(client.get("/api/ps"), 0.2)).json() == {"models": []}
    fake.ready = True
    result = await asyncio.wait_for(task, 2)
    assert result.status_code == 200
    assert result.json()["message"]["content"] == "Oi"
    loaded = (await client.get("/api/ps")).json()["models"][0]
    assert loaded["model"] == "gemma"
    assert datetime.fromisoformat(loaded["expires_at"]) > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_ps_reports_parseable_expiry_during_stream_and_indefinite_keep_alive(system):
    client, fake, _ = system
    fake.stream_started = asyncio.Event()
    fake.stream_continue = asyncio.Event()
    task = asyncio.create_task(client.post("/api/chat", json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "keep_alive": -1}))
    await asyncio.wait_for(fake.stream_started.wait(), 1)
    active = (await asyncio.wait_for(client.get("/api/ps"), 0.2)).json()["models"][0]
    assert active["model"] == active["name"] == "gemma"
    assert datetime.fromisoformat(active["expires_at"]) > datetime.now(timezone.utc)
    fake.stream_continue.set()
    assert (await task).status_code == 200
    pinned = (await client.get("/api/ps")).json()["models"][0]
    assert pinned["expires_at"] == "9999-12-31T23:59:59Z"


@pytest.mark.asyncio
async def test_proxy_start_does_not_load_model():
    fake = FakeUpstream()
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    app = create_app({"gemma": Model("gemma", "google/gemma-4-E2B")}, "2026-01-01T00:00:00Z", daemon_url="http://daemon", serve_url="http://serve", client=upstream)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.01)
        assert fake.starts == 0
        assert app.state.proxy.active == 0
        assert app.state.proxy.deadline is None
    await upstream.aclose()


@pytest.mark.asyncio
async def test_generate_and_unload(system):
    client, fake, _ = system
    result = await client.post("/api/generate", json={"model": "gemma", "prompt": "say hi", "stream": False})
    assert result.json()["response"] == "Olá"
    result = await client.post("/api/generate", json={"model": "gemma", "keep_alive": 0, "prompt": ""})
    assert result.json()["done_reason"] == "unload"
    assert fake.stops == 1
    assert (await client.get("/api/ps")).json() == {"models": []}


@pytest.mark.asyncio
async def test_generate_stream_and_unknown_model(system):
    client, _, _ = system
    missing = await client.post("/api/generate", json={"model": "missing", "prompt": "Oi"})
    assert missing.status_code == 404
    result = await client.post("/api/generate", json={"model": "gemma", "prompt": "Oi"})
    lines = [json.loads(line) for line in result.text.splitlines()]
    assert lines[0]["response"] == "Olá"
    assert lines[-1]["done"] is True


@pytest.mark.asyncio
async def test_idle_stop_and_negative_keep_alive(system):
    client, fake, app = system
    await client.post("/api/chat", json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "stream": False})
    await asyncio.sleep(1.1)
    assert fake.stops == 1
    await client.post("/api/chat", json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "stream": False, "keep_alive": -1})
    assert app.state.proxy.deadline is None
    await asyncio.sleep(1.1)
    assert fake.stops == 1


@pytest.mark.asyncio
async def test_stream_is_not_stopped_while_active(system):
    client, fake, _ = system
    fake.stream_started = asyncio.Event()
    fake.stream_continue = asyncio.Event()
    task = asyncio.create_task(client.post("/api/chat", json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "keep_alive": 0}))
    await asyncio.wait_for(fake.stream_started.wait(), 1)
    await asyncio.sleep(1.1)
    assert fake.stops == 0
    fake.stream_continue.set()
    assert (await task).status_code == 200
    assert fake.stops == 1


@pytest.mark.asyncio
async def test_loading_timeout_and_daemon_failure(system):
    client, fake, _ = system
    fake.ready = False
    body = {"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "stream": False}
    result = await client.post("/api/chat", json=body)
    assert result.status_code == 504
    fake.daemon_down = True
    result = await client.post("/api/chat", json=body)
    assert result.status_code == 503


@pytest.mark.asyncio
async def test_stop_failure_preserves_engine(system):
    client, fake, _ = system
    fake.stop_fails = True
    await client.post("/api/chat", json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}], "stream": False, "keep_alive": 0})
    assert fake.running is True
    assert (await client.get("/api/ps")).json()["models"][0]["name"] == "gemma"
