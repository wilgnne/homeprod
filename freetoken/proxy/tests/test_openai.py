"""HTTP lifecycle tests for OpenAI inference routes."""

import asyncio
import json

import pytest


class TestOpenAILifecycle:
    """Exercise OpenAI endpoints through the HTTP app."""

    @pytest.mark.asyncio
    async def test_openai_catalog_and_nonstreaming(self, system):
        client, fake, _ = system
        listed = (await client.get("/v1/models")).json()
        assert listed["object"] == "list"
        assert [model["id"] for model in listed["data"]] == ["gemma", "other"]
        assert fake.starts == 0
        assert (await client.get("/v1/models/gemma")).json()["id"] == "gemma"
        assert (await client.get("/v1/models/missing")).status_code == 404

        payload = {
            "model": "gemma",
            "messages": [{"role": "user", "content": "Oi"}],
            "temperature": 0.2,
            "stream": False,
        }
        result = await client.post("/v1/chat/completions", json=payload)
        assert result.status_code == 200
        assert result.json()["choices"][0]["message"]["content"] == "Oi"
        assert result.json()["model"] == "gemma"
        upstream = next(req for req in fake.requests if req.url.path == "/v1/chat/completions")
        sent = json.loads(upstream.content)
        assert sent["model"] == "gemma-runtime"
        assert sent["temperature"] == 0.2
        assert fake.starts == 1

        completion = await client.post(
            "/v1/completions", json={"model": "gemma", "prompt": "Oi"}
        )
        assert completion.json()["choices"][0]["text"] == "Olá"
        assert fake.starts == 1
        assert (
            await client.post(
                "/v1/chat/completions", json={"model": "other", "messages": []}
            )
        ).status_code == 409
        assert (
            await client.post(
                "/v1/chat/completions", json={"model": "missing", "messages": []}
            )
        ).status_code == 404
        await asyncio.sleep(1.1)
        assert fake.stops == 1

    @pytest.mark.asyncio
    async def test_openai_cold_start_shares_acquire(self, system):
        client, fake, app = system
        fake.ready = False
        app.state.proxy.start_timeout_seconds = 2
        payload = {
            "model": "gemma",
            "messages": [{"role": "user", "content": "Oi"}],
        }
        first = asyncio.create_task(
            client.post("/v1/chat/completions", json=payload)
        )
        second = asyncio.create_task(
            client.post("/v1/chat/completions", json=payload)
        )
        await asyncio.sleep(0.05)
        assert fake.starts == 1
        assert not any(req.url.path == "/v1/chat/completions" for req in fake.requests)
        fake.ready = True
        results = await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert all(result.status_code == 200 for result in results)
        assert fake.starts == 1

    @pytest.mark.asyncio
    async def test_openai_responses(self, system):
        client, fake, _ = system
        result = await client.post(
            "/v1/responses",
            json={"model": "gemma", "input": "Oi", "keep_alive": -1},
        )
        assert result.json()["object"] == "response"
        assert result.json()["model"] == "gemma"
        assert fake.starts == 1
        fake.chat_error = True
        error = await client.post(
            "/v1/chat/completions", json={"model": "gemma", "messages": []}
        )
        assert error.status_code == 400
        assert error.json()["error"]["message"] == "invalid request"

