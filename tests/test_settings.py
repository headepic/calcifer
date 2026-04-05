"""Tests for settings loading."""

import tempfile
from pathlib import Path

import pytest
import yaml

from calcifer.utils.settings.settings import _deep_merge
from calcifer.utils.settings import load_settings


def test_deep_merge_scalars():
    result = _deep_merge({"a": 1, "b": 2}, {"b": 3, "c": 4})
    assert result == {"a": 1, "b": 3, "c": 4}


def test_deep_merge_lists():
    result = _deep_merge({"a": [1, 2]}, {"a": [3, 4]})
    assert result == {"a": [1, 2, 3, 4]}


def test_deep_merge_nested():
    result = _deep_merge(
        {"a": {"x": 1, "y": 2}},
        {"a": {"y": 3, "z": 4}},
    )
    assert result == {"a": {"x": 1, "y": 3, "z": 4}}


def test_load_settings_from_project_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "calcifer.yaml"
        config_path.write_text(
            yaml.dump({
                "model": "gpt-4o-mini",
                "max_turns": 50,
                "mcp_servers": [
                    {"name": "test", "transport": "stdio", "command": "echo"},
                ],
            })
        )

        config = load_settings(project_dir=tmpdir)
        assert config.model == "gpt-4o-mini"
        assert config.max_turns == 50
        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0].name == "test"


def test_load_settings_defaults():
    with tempfile.TemporaryDirectory() as tmpdir:
        # No config file
        config = load_settings(project_dir=tmpdir)
        assert config.model == "gpt-4o"
        assert config.max_turns == 100


def test_load_settings_with_overrides():
    with tempfile.TemporaryDirectory() as tmpdir:
        config = load_settings(
            project_dir=tmpdir,
            overrides={"model": "claude-3-5-sonnet", "max_tokens": 4096},
        )
        assert config.model == "claude-3-5-sonnet"
        assert config.max_tokens == 4096
