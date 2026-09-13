"""Application configuration from environment variables."""

from __future__ import annotations

import os


class Settings:
    """Centralized configuration."""

    def __init__(self) -> None:
        self.model_catalog: str = os.getenv("FT_MODEL_CATALOG", "/app/models.json")
        self.daemon_url: str = os.getenv("FT_DAEMON_URL", "http://daemon:19000")
        self.serve_url: str = os.getenv("FT_SERVE_URL", "http://daemon:1919")
        self.token: str = os.getenv("FT_DAEMON_TOKEN", "")
        self.idle_seconds: float = float(os.getenv("FT_IDLE_SECONDS", "300"))
        self.start_timeout_seconds: float = float(os.getenv("FT_START_TIMEOUT_SECONDS", "900"))


_settings: Settings | None = None


def get_settings() -> Settings:
    """Lazy-initialized singleton for settings."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
