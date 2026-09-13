"""HTTP tests for engine, model catalog, and running-model routes."""

import asyncio

import pytest
from starlette.testclient import TestClient


class TestEngineRoutes:
    """Test engine management endpoints."""

    def test_root(self, app):
        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        assert "running" in response.text.lower()

    def test_version(self, app):
        client = TestClient(app)
        response = client.get("/api/version")
        assert response.status_code == 200
        data = response.json()
        assert "version" in data

    def test_tags(self, app):
        client = TestClient(app)
        response = client.get("/api/tags")
        assert response.status_code == 200
        data = response.json()
        assert "models" in data
        assert len(data["models"]) == 2
        assert any(m["name"] == "test/model" for m in data["models"])

    def test_openai_models(self, app):
        client = TestClient(app)
        response = client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) == 2

    def test_openai_model_detail(self, app):
        client = TestClient(app)
        response = client.get("/v1/models/test/model")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "test/model"

    def test_openai_model_not_found(self, app):
        client = TestClient(app)
        response = client.get("/v1/models/nonexistent")
        assert response.status_code == 404


class TestOllamaRoutes:
    """Test Ollama-compatible endpoints."""

    def test_show_valid_model(self, app):
        client = TestClient(app)
        response = client.post(
            "/api/show",
            json={"model": "test/model"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "details" in data
        assert "capabilities" in data

    def test_show_valid_model_name_field(self, app):
        """POST /api/show should accept 'name' as alias for 'model'."""
        client = TestClient(app)
        response = client.post(
            "/api/show",
            json={"name": "test/model"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "details" in data

    def test_show_invalid_model(self, app):
        client = TestClient(app)
        response = client.post(
            "/api/show",
            json={"model": "nonexistent"},
        )
        assert response.status_code == 404

    def test_show_malformed_json(self, app):
        """POST /api/show should return 400 on malformed JSON."""
        client = TestClient(app)
        response = client.post(
            "/api/show",
            content="not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400

    def test_show_array_body(self, app):
        """POST /api/show should return 400 when body is an array."""
        client = TestClient(app)
        response = client.post(
            "/api/show",
            json=[1, 2, 3],
        )
        assert response.status_code == 400


class TestPSDuringStream:
    """Tests for /api/ps reporting during streaming."""

    @pytest.mark.asyncio
    async def test_ps_reports_model_during_stream(self, system):
        from datetime import datetime, timezone

        client, fake, _ = system
        fake.stream_started = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests
        task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={"model": "gemma", "messages": [{"role": "user", "content": "Oi"}]},
            )
        )
        await asyncio.wait_for(fake.stream_started.wait(), 1)
        active = (await asyncio.wait_for(client.get("/api/ps"), 0.2)).json()["models"]
        assert len(active) == 1
        assert active[0]["model"] == active[0]["name"] == "gemma"
        assert datetime.fromisoformat(active[0]["expires_at"]) > datetime.now(timezone.utc)
        fake.stream_continue.set()
        assert (await task).status_code == 200
        # After streaming completes the default keep_alive is used (not -1),
        # so the model remains pinned for the idle period.
        active_after = (await client.get("/api/ps")).json()["models"]
        assert len(active_after) == 1
        assert active_after[0]["model"] == "gemma"
        assert datetime.fromisoformat(active_after[0]["expires_at"]) > datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_ps_pinned_with_negative_keep_alive(self, system):
        from datetime import datetime, timezone

        client, fake, _ = system
        fake.stream_started = asyncio.Event()
        fake.stream_continue = asyncio.Event()
        fake.stream_continue2 = asyncio.Event()  # for second request in P2 tests
        task = asyncio.create_task(
            client.post(
                "/api/chat",
                json={
                    "model": "gemma",
                    "messages": [{"role": "user", "content": "Oi"}],
                    "keep_alive": -1,
                },
            )
        )
        await asyncio.wait_for(fake.stream_started.wait(), 1)
        active = (await asyncio.wait_for(client.get("/api/ps"), 0.2)).json()["models"][0]
        assert datetime.fromisoformat(active["expires_at"]) > datetime.now(timezone.utc)
        fake.stream_continue.set()
        assert (await task).status_code == 200
        pinned = (await client.get("/api/ps")).json()["models"][0]
        assert pinned["expires_at"] == "9999-12-31T23:59:59Z"


