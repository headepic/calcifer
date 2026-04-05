"""Bash command security analysis.

Mirrors Claude Code's BashTool/bashSecurity.ts + bashPermissions.ts:
- AST-like command parsing for security classification
- sed edit detection
- git operation tracking
- cwd outside-project detection
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CommandAnalysis:
    """Result of analyzing a shell command for security."""

    commands: list[str]  # Individual commands in pipeline
    is_read_only: bool
    is_destructive: bool
    has_sudo: bool
    has_pipe: bool
    has_redirect: bool
    writes_to_files: list[str]  # Detected file write targets
    git_operations: list[str]  # e.g. ["commit", "push"]
    sed_edits: list[str]  # Files edited by sed -i
    cd_targets: list[str]  # Directories cd'd into
    warnings: list[str]


# Commands that are known to be safe (read-only)
SAFE_COMMANDS = {
    "cat", "head", "tail", "less", "more", "wc", "stat", "file", "strings",
    "ls", "tree", "du", "df", "find", "which", "whereis", "type", "realpath",
    "grep", "rg", "ag", "ack", "locate",
    "echo", "printf", "true", "false", "test", "[",
    "pwd", "whoami", "hostname", "date", "env", "printenv", "uname",
    "sort", "uniq", "cut", "tr", "awk", "sed", "jq", "yq", "xq",
    "diff", "comm", "cmp", "md5sum", "sha256sum",
    "python --version", "python3 --version", "node --version",
    "pip list", "pip show", "npm list", "cargo --version",
    "xargs",
}

# Commands that write/modify
WRITE_COMMANDS = {
    "rm", "rmdir", "mv", "cp", "mkdir", "touch", "chmod", "chown", "chgrp",
    "ln", "install", "rsync",
    "tee", "dd",
    "pip install", "npm install", "yarn add", "cargo add",
}

# Git operations by category
GIT_READ_OPS = {"status", "log", "diff", "show", "branch", "tag", "remote", "stash list", "blame", "shortlog"}
GIT_WRITE_OPS = {"commit", "push", "merge", "rebase", "reset", "checkout", "pull", "cherry-pick", "stash pop", "stash drop"}
GIT_DESTRUCTIVE_OPS = {"push --force", "reset --hard", "clean -f", "branch -D"}


def parse_command(command: str) -> CommandAnalysis:
    """Parse a shell command and analyze it for security properties."""
    analysis = CommandAnalysis(
        commands=[], is_read_only=True, is_destructive=False,
        has_sudo=False, has_pipe=False, has_redirect=False,
        writes_to_files=[], git_operations=[], sed_edits=[],
        cd_targets=[], warnings=[],
    )

    # Split on pipes, &&, ||, ;
    segments = re.split(r'\s*(?:\||\|\||&&|;)\s*', command.strip())
    analysis.has_pipe = "|" in command

    # Detect redirects
    if re.search(r'[^2]>(?!>)|>>|2>', command):
        analysis.has_redirect = True
        analysis.is_read_only = False

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        analysis.commands.append(segment)
        parts = segment.split()
        if not parts:
            continue

        base_cmd = parts[0]

        # sudo detection
        if base_cmd == "sudo":
            analysis.has_sudo = True
            analysis.warnings.append("Uses sudo")
            parts = parts[1:]
            base_cmd = parts[0] if parts else ""

        # cd detection
        if base_cmd == "cd" and len(parts) > 1:
            analysis.cd_targets.append(parts[1])

        # git operation tracking
        if base_cmd == "git" and len(parts) > 1:
            git_subcmd = parts[1]
            full_git = " ".join(parts[1:3]) if len(parts) > 2 else git_subcmd
            analysis.git_operations.append(git_subcmd)

            if full_git in GIT_DESTRUCTIVE_OPS or any(
                full_git.startswith(d) for d in GIT_DESTRUCTIVE_OPS
            ):
                analysis.is_destructive = True
                analysis.warnings.append(f"Destructive git operation: {full_git}")
            elif git_subcmd in GIT_WRITE_OPS:
                analysis.is_read_only = False

        # sed -i detection
        if base_cmd == "sed" and "-i" in parts:
            analysis.is_read_only = False
            # Find the file arguments (after the pattern)
            for i, p in enumerate(parts):
                if i > 0 and not p.startswith("-") and "/" not in p:
                    analysis.sed_edits.append(p)

        # Write command detection
        if base_cmd in WRITE_COMMANDS:
            analysis.is_read_only = False

        two_word = f"{parts[0]} {parts[1]}" if len(parts) > 1 else ""
        if two_word in WRITE_COMMANDS:
            analysis.is_read_only = False

        # Destructive patterns
        if base_cmd == "rm" and any(f in parts for f in ["-rf", "-fr", "-r"]):
            analysis.is_destructive = True
            # Check for dangerous targets
            for p in parts[1:]:
                if not p.startswith("-") and (p == "/" or p.startswith("/") and p.count("/") <= 2):
                    analysis.warnings.append(f"rm targeting system path: {p}")

    return analysis


def check_cwd_outside_project(cwd: str, project_root: str) -> bool:
    """Check if cwd has escaped the project directory."""
    try:
        cwd_resolved = Path(cwd).resolve()
        root_resolved = Path(project_root).resolve()
        return not str(cwd_resolved).startswith(str(root_resolved))
    except Exception:
        return False
