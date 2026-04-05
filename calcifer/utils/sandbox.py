"""Sandbox: isolated command execution.

Mirrors Claude Code's utils/sandbox/sandbox-adapter.ts:
- Configurable sandbox backends (none, firejail, docker, macos-sandbox)
- Path allowlist/denylist
- Network restriction
- Read-only filesystem regions
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SandboxBackend(str, Enum):
    NONE = "none"           # No sandboxing
    FIREJAIL = "firejail"   # Linux firejail
    MACOS = "macos"         # macOS sandbox-exec
    DOCKER = "docker"       # Docker container


@dataclass
class SandboxConfig:
    """Sandbox configuration."""

    backend: SandboxBackend = SandboxBackend.NONE
    allowed_paths: list[str] = field(default_factory=list)  # Read/write access
    read_only_paths: list[str] = field(default_factory=list)  # Read-only access
    denied_paths: list[str] = field(default_factory=list)  # No access
    allow_network: bool = True
    docker_image: str = "ubuntu:latest"


def _is_available(backend: SandboxBackend) -> bool:
    """Check if a sandbox backend is available on this system."""
    if backend == SandboxBackend.NONE:
        return True
    if backend == SandboxBackend.FIREJAIL:
        return shutil.which("firejail") is not None
    if backend == SandboxBackend.MACOS:
        return os.uname().sysname == "Darwin" and shutil.which("sandbox-exec") is not None
    if backend == SandboxBackend.DOCKER:
        return shutil.which("docker") is not None
    return False


class SandboxManager:
    """Manages sandboxed command execution."""

    def __init__(self, config: SandboxConfig | None = None):
        self._config = config or SandboxConfig()

        if self._config.backend != SandboxBackend.NONE:
            if not _is_available(self._config.backend):
                logger.warning(
                    "Sandbox backend %s not available, falling back to none",
                    self._config.backend.value,
                )
                self._config.backend = SandboxBackend.NONE

    @property
    def is_sandboxed(self) -> bool:
        return self._config.backend != SandboxBackend.NONE

    def should_sandbox(self, command: str) -> bool:
        """Decide if a command should be sandboxed.

        Skip sandboxing for simple read-only commands.
        """
        if self._config.backend == SandboxBackend.NONE:
            return False

        # Excluded patterns (sandbox overhead not worth it)
        excluded = {"echo", "printf", "true", "false", "test", "pwd", "whoami", "date"}
        first_cmd = command.strip().split()[0] if command.strip() else ""
        return first_cmd not in excluded

    def wrap_command(self, command: str, cwd: str = ".") -> str:
        """Wrap a command with sandbox invocation."""
        if not self.should_sandbox(command):
            return command

        if self._config.backend == SandboxBackend.FIREJAIL:
            return self._firejail_wrap(command, cwd)
        elif self._config.backend == SandboxBackend.MACOS:
            return self._macos_wrap(command, cwd)
        elif self._config.backend == SandboxBackend.DOCKER:
            return self._docker_wrap(command, cwd)

        return command

    def _firejail_wrap(self, command: str, cwd: str) -> str:
        parts = ["firejail", "--quiet", "--noprofile"]

        if not self._config.allow_network:
            parts.append("--net=none")

        for path in self._config.allowed_paths:
            parts.append(f"--whitelist={path}")

        for path in self._config.read_only_paths:
            parts.append(f"--read-only={path}")

        for path in self._config.denied_paths:
            parts.append(f"--blacklist={path}")

        parts.append("--")
        parts.append("bash")
        parts.append("-c")
        parts.append(command)

        return " ".join(parts)

    def _macos_wrap(self, command: str, cwd: str) -> str:
        # macOS sandbox-exec with a profile
        allow_rules: list[str] = [
            "(allow default)",
        ]
        for path in self._config.denied_paths:
            allow_rules.append(f'(deny file-write* (subpath "{path}"))')
            allow_rules.append(f'(deny file-read-data (subpath "{path}"))')

        if not self._config.allow_network:
            allow_rules.append("(deny network*)")

        profile = "\n".join(["(version 1)"] + allow_rules)
        # Write profile to temp file
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sb", delete=False) as f:
            f.write(profile)
            profile_path = f.name

        return f'sandbox-exec -f {profile_path} bash -c {_shell_quote(command)}'

    def _docker_wrap(self, command: str, cwd: str) -> str:
        parts = [
            "docker", "run", "--rm",
            "-w", "/workspace",
            "-v", f"{os.path.abspath(cwd)}:/workspace",
        ]

        for path in self._config.allowed_paths:
            parts.extend(["-v", f"{path}:{path}"])

        for path in self._config.read_only_paths:
            parts.extend(["-v", f"{path}:{path}:ro"])

        if not self._config.allow_network:
            parts.append("--network=none")

        parts.append(self._config.docker_image)
        parts.extend(["bash", "-c", command])

        return " ".join(parts)


def _shell_quote(s: str) -> str:
    """Quote a string for shell use."""
    import shlex
    return shlex.quote(s)
