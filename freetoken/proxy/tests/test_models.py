"""Tests for the proxy service (unit tests)."""

import pytest

from proxy.models import read_catalog


class TestReadCatalog:
    """Test catalog reading and validation."""

    def test_read_valid_catalog(self, tmp_path):
        catalog_file = tmp_path / "models.json"
        catalog_file.write_text(
            '{"models": [{"name": "test/model", "model": "test/model"}]}'
        )
        catalog, modified = read_catalog(str(catalog_file))
        assert "test/model" in catalog
        assert modified

    def test_invalid_catalog_missing_models(self, tmp_path):
        catalog_file = tmp_path / "invalid.json"
        catalog_file.write_text('{"invalid": true}')
        with pytest.raises(ValueError):
            read_catalog(str(catalog_file))

    def test_duplicate_model_raises(self, tmp_path):
        catalog_file = tmp_path / "dup.json"
        catalog_file.write_text(
            '{"models": [{"name": "dup", "model": "m1"}, {"name": "dup", "model": "m2"}]}'
        )
        with pytest.raises(ValueError):
            read_catalog(str(catalog_file))

    def test_disallowed_args_raises(self, tmp_path):
        catalog_file = tmp_path / "disallowed.json"
        catalog_file.write_text(
            '{"models": [{"name": "test/model", "model": "test/model", "args": ["--host", "0.0.0.0"]}]}',
        )
        with pytest.raises(ValueError):
            read_catalog(str(catalog_file))
