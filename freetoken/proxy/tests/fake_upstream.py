"""Mock daemon and inference server for HTTP integration tests."""

import json

import httpx


class GatedStream(httpx.AsyncByteStream):
    """Stream that gates on an event before yielding remaining chunks."""

    def __init__(self, fake):
        self.fake = fake

    async def __aiter__(self):
        self.fake.stream_started.set()
        yield b'data: {"model":"gemma-runtime","choices":[{"delta":{"content":"Oi"}}]}\n\n'
        # Use stream_continue2 for second request, stream_continue for first
        cont_event = self.fake.stream_continue2 if self.fake._stream_req_count > 1 else self.fake.stream_continue
        if cont_event is not None:
            await cont_event.wait()
        yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


class FakeUpstream:
    """Mock HTTP transport that simulates a FreeToken daemon + serve stack."""

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
        self.stream_blocked = None
        self.stream_continue = None
        self.stream_continue2 = None  # separate event for second request
        self.chat_error = False
        self.block_all = False
        self.acquire_entered = None
        self.before_stream_body = None
        self._stream_req_count = 0  # counter for streaming requests

    async def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "daemon":
            if self.daemon_down:
                raise httpx.ConnectError("daemon down")
            if path == "/engine/status":
                return httpx.Response(
                    200,
                    json={"running": self.running, "model": self.model, "port": 1919},
                )
            if path == "/engine/start":
                body = json.loads(request.content)
                self.starts += 1
                self.running = True
                self.model = body["model"]
                return httpx.Response(200, json={"pid": 123})
            if path == "/engine/stop":
                self.stops += 1
                if self.stop_fails:
                    return httpx.Response(
                        503, json={"error": "accounting failed", "enginePreserved": True}
                    )
                self.running = False
                return httpx.Response(200, json={"already": False})
        if path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gemma-runtime"}]})
        if path == "/health":
            return httpx.Response(
                200,
                json={
                    "status": "ok" if self.ready else "loading",
                    "maintenance": "serving" if self.ready else "loading",
                },
            )
        if path == "/v1/chat/completions":
            if self.chat_error:
                return httpx.Response(
                    400, json={"error": {"message": "invalid request", "type": "invalid_request_error"}}
                )
            # Signal that upstream() is called — second signal before body for "cancel before headers"
            if self.stream_blocked is not None:
                self.stream_blocked.set()
            if self.before_stream_body is not None:
                await self.before_stream_body.wait()
            # Use stream_continue for first request, stream_continue2 for second
            self._stream_req_count += 1
            cont_event = self.stream_continue2 if self._stream_req_count > 1 else self.stream_continue
            if cont_event is not None:
                return httpx.Response(
                    200,
                    stream=GatedStream(self),
                    headers={"content-type": "text/event-stream"},
                )
            if json.loads(request.content).get("stream", False):
                data = (
                    'data: {"model":"gemma-runtime","choices":[{"delta":{"content":"Oi"},"finish_reason":null}]}'
                    '\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                    '"usage":{"prompt_tokens":3,"completion_tokens":1}}\n\ndata: [DONE]\n\n'
                )
                return httpx.Response(200, content=data, headers={"content-type": "text/event-stream"})
            return httpx.Response(
                200,
                json={
                    "model": "gemma-runtime",
                    "choices": [{"message": {"content": "Oi"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )
        if path == "/v1/completions":
            if self.stream_blocked is not None:
                self.stream_blocked.set()
            if self.before_stream_body is not None:
                await self.before_stream_body.wait()
            if self.stream_continue is not None:
                return httpx.Response(
                    200,
                    stream=GatedStream(self),
                    headers={"content-type": "text/event-stream"},
                )
            if json.loads(request.content).get("stream", False):
                data = (
                    'data: {"choices":[{"text":"Olá","finish_reason":null}]}\n\n'
                    'data: [DONE]\n\n'
                )
                return httpx.Response(200, content=data, headers={"content-type": "text/event-stream"})
            return httpx.Response(
                200,
                json={
                    "choices": [{"text": "Olá", "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                },
            )
        if path == "/v1/responses":
            if self.stream_blocked is not None:
                self.stream_blocked.set()
            if self.before_stream_body is not None:
                await self.before_stream_body.wait()
            if self.stream_continue is not None:
                return httpx.Response(
                    200,
                    stream=GatedStream(self),
                    headers={"content-type": "text/event-stream"},
                )
            if json.loads(request.content).get("stream", False):
                data = (
                    'event: response.output_text.delta\n'
                    'data: {"type":"response.output_text.delta","delta":"Oi"}\n\n'
                    'event: response.completed\n'
                    'data: {"type":"response.completed","response":{"model":"gemma-runtime","status":"completed"}}\n\n'
                )
                return httpx.Response(200, content=data, headers={"content-type": "text/event-stream"})
            return httpx.Response(
                200,
                json={
                    "id": "resp_1",
                    "object": "response",
                    "model": "gemma-runtime",
                    "status": "completed",
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "Oi"}]}],
                },
            )
        return httpx.Response(404)

