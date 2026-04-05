"""Settings: YAML config loading with two-level merge (user + project).

Lightweight equivalent of Claude Code's Settings System.
User config: ~/.calcifer/config.yaml
Project config: ./calcifer.yaml
Project overrides user.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ...config import CalciferConfig, MCPServerConfig

USER_CONFIG_DIR = Path.home() / ".calcifer"
USER_CONFIG_FILE = USER_CONFIG_DIR / "config.yaml"
PROJECT_CONFIG_FILE = "calcifer.yaml"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts. Override wins for scalar values, lists concatenate."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            result[key] = result[key] + value
        else:
            result[key] = value
    return result


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML file, return empty dict if not found."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_mcp_servers(raw: list[dict[str, Any]] | None) -> list[MCPServerConfig]:
    """Parse MCP server configs from raw YAML data."""
    if not raw:
        return []
    servers: list[MCPServerConfig] = []
    for entry in raw:
        servers.append(
            MCPServerConfig(
                name=entry.get("name", ""),
                transport=entry.get("transport", "stdio"),
                command=entry.get("command"),
                args=entry.get("args", []),
                env=entry.get("env", {}),
                url=entry.get("url"),
            )
        )
    return servers


def _dict_to_config(data: dict[str, Any]) -> CalciferConfig:
    """Convert a merged config dict to CalciferConfig."""
    return CalciferConfig(
        api_key=data.get("api_key", os.environ.get("OPENAI_API_KEY", "")),
        base_url=data.get("base_url", "https://api.openai.com/v1"),
        model=data.get("model", "gpt-4o"),
        max_tokens=data.get("max_tokens", 8192),
        temperature=data.get("temperature", 0.0),
        max_turns=data.get("max_turns", 100),
        system_prompt=data.get("system_prompt", ""),
        max_tool_concurrency=data.get("max_tool_concurrency", 10),
        max_context_tokens=data.get("max_context_tokens", 128_000),
        compact_threshold=data.get("compact_threshold", 0.9),
        mcp_servers=_parse_mcp_servers(data.get("mcp_servers")),
        skills_dirs=data.get("skills_dirs", []),
        memory_dir=data.get("memory_dir"),
        extra_api_params=data.get("extra_api_params", {}),
    )


def load_settings(
    project_dir: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> CalciferConfig:
    """Load settings from user and project config files.

    Priority (low → high): defaults → user config → project config → overrides.
    """
    # Load user config
    user_data = _load_yaml_file(USER_CONFIG_FILE)

    # Load project config
    project_path = Path(project_dir or ".") / PROJECT_CONFIG_FILE
    project_data = _load_yaml_file(project_path)

    # Merge: user ← project ← overrides
    merged = _deep_merge(user_data, project_data)
    if overrides:
        merged = _deep_merge(merged, overrides)

    return _dict_to_config(merged)


def save_user_settings(data: dict[str, Any]) -> None:
    """Save settings to user config file."""
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(USER_CONFIG_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False)
