"""Tests for the FastAPI app factory."""

from unittest.mock import patch

from proxy.main import create_app


class TestAppFactory:
    """Test the app factory path without overriding app.state.proxy."""

    def test_idle_seconds_zero_is_respected(self, catalog_path):
        """create_app(idle_seconds=0) must keep idle_seconds at 0, not fall back to env."""
        with patch.dict(
            "os.environ",
            {"FT_MODEL_CATALOG": catalog_path},
            clear=True,
        ):
            import proxy.config as config_mod
            config_mod._settings = None
            app = create_app(idle_seconds=0, start_timeout_seconds=0)
            assert app.state.proxy.idle_seconds == 0
            assert app.state.proxy.start_timeout_seconds == 0

    def test_idle_seconds_explicit_value(self, catalog_path):
        """create_app(idle_seconds=5) overrides any environment default."""
        with patch.dict(
            "os.environ",
            {"FT_MODEL_CATALOG": catalog_path, "FT_IDLE_SECONDS": "999"},
            clear=True,
        ):
            import proxy.config as config_mod
            config_mod._settings = None
            app = create_app(idle_seconds=5)
            assert app.state.proxy.idle_seconds == 5

    def test_create_app_from_catalog(self, catalog_path):
        """create_app() should load catalog from FT_MODEL_CATALOG."""
        with patch.dict(
            "os.environ",
            {
                "FT_MODEL_CATALOG": catalog_path,
                "FT_DAEMON_URL": "http://mock:19000",
                "FT_SERVE_URL": "http://mock:1919",
            },
            clear=True,
        ):
            # Reset the settings singleton so env vars are re-read
            import proxy.config as config_mod
            config_mod._settings = None
            app = create_app()
            proxy = app.state.proxy
