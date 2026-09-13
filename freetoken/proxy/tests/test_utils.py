"""Tests for utility functions."""

import pytest

from proxy.utils import (
    keep_alive_seconds,
    utc_now,
    model_details,
    tag_info,
    options_to_openai,
    ollama_tools,
)


class TestKeepAliveSeconds:
    """Test keep_alive parsing."""

    def test_none_returns_default(self):
        assert keep_alive_seconds(None, 300) == 300

    def test_number(self):
        assert keep_alive_seconds(60, 300) == 60

    def test_string_suffix(self):
        assert keep_alive_seconds("5m", 300) == 300
        assert keep_alive_seconds("1h", 300) == 3600
        assert keep_alive_seconds("5s", 300) == 5
        assert keep_alive_seconds("500ms", 300) == 0.5

    def test_zero_is_finite(self):
        assert keep_alive_seconds(0, 300) == 0

    def test_negative_returns_none(self):
        assert keep_alive_seconds(-1, 300) is None


class TestOptionsToOpenAI:
    """Test options mapping."""

    def test_map_options(self):
        body = {
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                "num_predict": 128,
            },
            "format": "json",
        }
        result = options_to_openai(body)
        assert result["temperature"] == 0.7
        assert result["top_p"] == 0.9
        assert result["max_tokens"] == 128
        assert result["response_format"] == {"type": "json_object"}

    def test_missing_options(self):
        result = options_to_openai({})
        assert result == {}


class TestOllamaTools:
    """Test tool call conversion."""

    def test_convert_tool_calls(self):
        result = ollama_tools([{"function": {"name": "get_weather", "arguments": '{"city": "NYC"}'}}])
        assert len(result) == 1
        assert result[0]["function"]["name"] == "get_weather"
        assert result[0]["function"]["arguments"]["city"] == "NYC"
