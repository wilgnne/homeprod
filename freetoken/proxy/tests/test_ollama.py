"""HTTP lifecycle and error tests for Ollama inference routes."""

import asyncio
import json

import pytest


class TestOLlamaLifecycle:
    """Exercise Ollama chat and generate through the HTTP app."""

    @pytest.mark.asyncio
    async def test_catalog_without_running_engine(self, system):
        client, fake, _ = system
        response = await client.get("/api/tags")
        assert response.status_code == 200
        assert [m["name"] for m in response.json()["models"]] == ["gemma", "other"]
        assert response.json()["models"][0]["details"]["format"] == "freetoken"
        assert fake.starts == 0
        assert (await client.get("/api/ps")).json() == {"models": []}
        assert (await client.post("/api/show", json={"model": "gemma"})).json()["capabilities"] == [
            "completion"
        ]

    @pytest.mark.asyncio
    async def test_chat_start_stream_and_busy_model(self, system):
        client, fake, _ = system
        result = await client.post(
            "/api/chat",
            json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}]},
        )
        assert result.status_code == 200
        lines = [json.loads(line) for line in result.text.splitlines()]
        assert lines[0]["message"]["content"] == "Oi"
        assert lines[-1]["done"] is True
        assert lines[-1]["eval_count"] == 1
        assert fake.starts == 1
        active = (await client.get("/api/ps")).json()["models"]
        assert active[0]["name"] == "gemma"
        busy = await client.post(
            "/api/chat",
            json={"model": "other", "messages": [{"role": "user", "content": "Oi"}], "stream": False},
        )
        assert busy.status_code == 409

    @pytest.mark.asyncio
    async def test_concurrent_start_is_single_flight(self, system):
        client, fake, _ = system
        body = {
            "model": "gemma",
            "messages": [{"role": "user", "content": "Oi"}],
            "stream": False,
        }
        first, second = await asyncio.gather(
            client.post("/api/chat", json=body),
            client.post("/api/chat", json=body),
        )
        assert first.status_code == second.status_code == 200
        assert first.json()["message"]["content"] == "Oi"
        assert fake.starts == 1

    @pytest.mark.asyncio
    async def test_generate_and_unload(self, system):
        client, fake, _ = system
        result = await client.post(
            "/api/generate", json={"model": "gemma", "prompt": "say hi", "stream": False}
        )
        assert result.json()["response"] == "Olá"
        result = await client.post(
            "/api/generate", json={"model": "gemma", "keep_alive": 0, "prompt": ""}
        )
        assert result.json()["done_reason"] == "unload"
        assert fake.stops == 1
        assert (await client.get("/api/ps")).json() == {"models": []}

    @pytest.mark.asyncio
    async def test_generate_stream_and_unknown_model(self, system):
        client, _, _ = system
        missing = await client.post(
            "/api/generate", json={"model": "missing", "prompt": "Oi"}
        )
        assert missing.status_code == 404
        result = await client.post(
            "/api/generate", json={"model": "gemma", "prompt": "Oi"}
        )
        lines = [json.loads(line) for line in result.text.splitlines()]
        assert lines[0]["response"] == "Olá"
        assert lines[-1]["done"] is True


class TestErrorHandling:
    """Tests for upstream errors and timeouts."""

    @pytest.mark.asyncio
    async def test_loading_timeout(self, system):
        client, fake, app = system
        fake.ready = False
        app.state.proxy.start_timeout_seconds = 2
        body = {
            "model": "gemma",
            "messages": [{"role": "user", "content": "Oi"}],
            "stream": False,
        }
        result = await client.post("/api/chat", json=body)
        assert result.status_code == 504

    @pytest.mark.asyncio
    async def test_daemon_failure(self, system):
        client, fake, app = system
        fake.daemon_down = True
        body = {
            "model": "gemma",
            "messages": [{"role": "user", "content": "Oi"}],
            "stream": False,
        }
        result = await client.post("/api/chat", json=body)
        assert result.status_code == 503

