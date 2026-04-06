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


# ────────────────────────────────────────────────────────────────────
# base_url resolver tests (sdk-config-env-defaults)
# ────────────────────────────────────────────────────────────────────


def test_config_base_url_default_is_none():
    """CalciferConfig.base_url default is None — Agent resolves it at init."""
    from calcifer.config import CalciferConfig
    cfg = CalciferConfig()
    assert cfg.base_url is None, (
        f"base_url default should be None, got {cfg.base_url!r}"
    )


def test_config_base_url_explicit_wins(monkeypatch):
    """Explicit kwarg beats both env var and canonical fallback."""
    from calcifer import Agent

    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example.com/v1")
    agent = Agent(base_url="https://explicit.example.com/v1", api_key="x")
    try:
        assert agent._config.base_url == "https://explicit.example.com/v1"
    finally:
        # close() is async, so just drop the reference; tests don't await
        pass


def test_config_base_url_env_fallback(monkeypatch):
    """When kwarg is None and OPENAI_BASE_URL is set, env wins over fallback."""
    from calcifer import Agent

    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example.com/v1")
    agent = Agent(api_key="x")
    assert agent._config.base_url == "https://env.example.com/v1"


def test_config_base_url_canonical_fallback(monkeypatch):
    """When neither kwarg nor env is set, fall back to api.openai.com."""
    from calcifer import Agent

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    agent = Agent(api_key="x")
    assert agent._config.base_url == "https://api.openai.com/v1"


def test_resolve_base_url_unit():
    """Direct unit test of the _resolve_base_url helper."""
    from calcifer.agent import _resolve_base_url
    import os

    # Save and restore env
    saved = os.environ.pop("OPENAI_BASE_URL", None)
    try:
        # Explicit wins over everything
        assert _resolve_base_url("https://explicit.test/v1") == "https://explicit.test/v1"

        # No env, no explicit → canonical fallback
        assert _resolve_base_url(None) == "https://api.openai.com/v1"
        assert _resolve_base_url("") == "https://api.openai.com/v1"

        # Env set, no explicit → env wins
        os.environ["OPENAI_BASE_URL"] = "https://env.test/v1"
        assert _resolve_base_url(None) == "https://env.test/v1"

        # Env set + explicit → explicit wins
        assert _resolve_base_url("https://override.test/v1") == "https://override.test/v1"
    finally:
        if saved is not None:
            os.environ["OPENAI_BASE_URL"] = saved
        else:
            os.environ.pop("OPENAI_BASE_URL", None)
