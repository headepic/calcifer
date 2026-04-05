"""Auto classifier: transcript-based security classification.

Mirrors Claude Code's TRANSCRIPT_CLASSIFIER:
- Analyzes tool call transcripts for security risk
- Categorizes as safe/suspicious/dangerous
- Used for auto-mode permission decisions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..tool import Tool
from ..types.message import Message

logger = logging.getLogger(__name__)


class SecurityLevel:
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    DANGEROUS = "dangerous"


@dataclass
class ClassificationResult:
    """Result of security classification."""

    level: str  # safe, suspicious, dangerous
    reason: str = ""
    tool_name: str = ""
    confidence: float = 1.0


# Tool categories for rule-based classification
ALWAYS_SAFE_TOOLS = {
    "file_read", "glob", "grep",  # Read-only tools
}

POTENTIALLY_DANGEROUS_TOOLS = {
    "bash", "file_write", "file_edit",
}

# Dangerous command patterns for bash
DANGEROUS_BASH_PATTERNS = [
    "rm -rf",
    "sudo",
    "curl.*|.*sh",
    "wget.*|.*sh",
    "> /dev/",
    "chmod 777",
    "pkill",
    "kill -9",
    "DROP TABLE",
    "DELETE FROM",
    "git push --force",
    "git reset --hard",
]


def classify_tool_call(
    tool_name: str,
    tool_input: dict[str, Any],
    tools_by_name: dict[str, Tool] | None = None,
) -> ClassificationResult:
    """Classify a single tool call for security risk.

    Rule-based fast classifier (no LLM needed).
    """
    # Always safe tools
    if tool_name in ALWAYS_SAFE_TOOLS:
        return ClassificationResult(level=SecurityLevel.SAFE, tool_name=tool_name)

    # Get auto-classifier input from tool
    if tools_by_name and tool_name in tools_by_name:
        tool = tools_by_name[tool_name]
        classifier_input = tool.to_auto_classifier_input(tool_input)
    else:
        classifier_input = str(tool_input)

    # Check dangerous patterns
    if tool_name == "bash":
        command = tool_input.get("command", "")
        for pattern in DANGEROUS_BASH_PATTERNS:
            if pattern.lower() in command.lower():
                return ClassificationResult(
                    level=SecurityLevel.DANGEROUS,
                    reason=f"Dangerous command pattern: {pattern}",
                    tool_name=tool_name,
                )

    # File operations: check paths
    if tool_name in ("file_write", "file_edit"):
        path = tool_input.get("file_path", "")
        sensitive_paths = [
            ".env", "credentials", ".ssh/", ".aws/",
            "/etc/passwd", "/etc/shadow",
            ".git/config", ".claude/",
        ]
        for sensitive in sensitive_paths:
            if sensitive in path:
                return ClassificationResult(
                    level=SecurityLevel.SUSPICIOUS,
                    reason=f"Operating on sensitive path: {path}",
                    tool_name=tool_name,
                )

    # Default: suspicious if potentially dangerous tool, safe otherwise
    if tool_name in POTENTIALLY_DANGEROUS_TOOLS:
        return ClassificationResult(
            level=SecurityLevel.SAFE,
            tool_name=tool_name,
            confidence=0.8,
        )

    return ClassificationResult(level=SecurityLevel.SAFE, tool_name=tool_name)


def classify_transcript(
    messages: list[Message],
    tools_by_name: dict[str, Tool] | None = None,
) -> list[ClassificationResult]:
    """Classify all tool calls in a conversation transcript."""
    results: list[ClassificationResult] = []

    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                import json
                try:
                    args = json.loads(tc.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = classify_tool_call(tc.function_name, args, tools_by_name)
                results.append(result)

    return results


def has_dangerous_calls(messages: list[Message], **kwargs: Any) -> bool:
    """Quick check if any dangerous tool calls exist in transcript."""
    results = classify_transcript(messages, **kwargs)
    return any(r.level == SecurityLevel.DANGEROUS for r in results)
