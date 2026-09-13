# FreeToken Proxy

Refactored FastAPI application with modular package structure.

## Project Structure

```
freetoken/proxy/
├── proxy/                          # Main package
│   ├── __init__.py                 # Package metadata
│   ├── main.py                     # App factory & lifespan
│   ├── config.py                   # Environment configuration
│   ├── models.py                   # Domain models & catalog
│   ├── schemas.py                  # Pydantic request/response schemas
│   ├── utils.py                    # Shared utility functions
│   ├── dependencies.py             # FastAPI dependencies
│   ├── proxy_service.py            # Business logic (ProxyService)
│   └── routers/                    # API route modules
│       ├── __init__.py
│       ├── engine.py               # /engine status endpoints
│       ├── ollama.py               # /api/chat, /api/generate
│       └── openai.py               # /v1/* endpoints
├── tests/                          # Test suite
│   ├── __init__.py
│   ├── conftest.py                 # Shared fixtures
│   ├── fake_upstream.py            # Shared mock HTTP transport
│   ├── test_main.py               # App factory tests
│   ├── test_engine.py             # Engine and catalog routes
│   ├── test_ollama.py             # Ollama API tests
│   ├── test_openai.py             # OpenAI API tests
│   ├── test_proxy_service.py      # Model lifecycle tests
│   ├── test_proxy_service_acquire.py       # Acquire cancellation tests
│   ├── test_proxy_service_stream_release.py # Stream release tests
│   ├── test_models.py              # Catalog validation tests
│   └── test_utils.py               # Utility function tests
├── pyproject.toml                  # Package metadata & config
├── requirements.txt                # Production dependencies
├── requirements-dev.txt            # Development dependencies
├── Dockerfile                      # Container image
└── .dockerignore
```

## Architecture

The refactored code follows the **FastAPI recommended project layout** with clear separation of concerns:

| Module | Responsibility |
|--------|---------------|
| `config.py` | Centralized configuration from environment variables |
| `models.py` | Domain models (Model dataclass, catalog parsing) |
| `schemas.py` | Pydantic request/response validation schemas |
| `utils.py` | Shared utilities (keep_alive, sse_json, options mapping) |
| `proxy_service.py` | Business logic - daemon/serve communication, model lifecycle |
| `dependencies.py` | FastAPI dependency injection helpers |
| `routers/` | API endpoint handlers (engine, ollama, openai) |
| `main.py` | App factory, lifespan management, router registration |

## Running

```bash
# Install dependencies
pip install -r requirements.txt

# Run with uvicorn
uvicorn proxy.main:app --host 0.0.0.0 --port 11434
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/
```

## Docker

```bash
docker build -t freetoken-proxy .
docker run -p 11434:11434 freetoken-proxy
```
