"""Context management: multi-layer compaction, token counting, result budget.

Mirrors Claude Code's services/compact/:
- Token counting via tiktoken or API-reported usage
- Token warning states with absolute buffer thresholds
- Tool result budget: cap aggregate tool output per conversation
- Snip compaction: trim oldest messages beyond a threshold
- Microcompact: per-tool-result truncation for large outputs
- Autocompact: LLM-based summarization when approaching context limit
- Context collapse: selective folding of tool call regions
- Reactive compact: emergency path on prompt_too_long
- Post-compact restoration: re-inject skills, MCP, file contents
- Compact boundary messages: mark compaction points for LLM awareness
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ...types.message import CompactBoundaryMessage, Message, Usage

logger = logging.getLogger(__name__)

# Compaction prompt — structured to prevent losing user intent and pending work
COMPACT_SYSTEM_PROMPT = """\
You are a conversation summarizer. You have no tools available — \
produce ONLY text output, do NOT attempt to call any tools.

Produce a structured summary using the exact sections below. \
Be concise but never omit information the assistant needs to \
continue effectively.

First, wrap your analysis in <analysis> tags (this will be stripped). \
Then output the summary in <summary> tags using these sections:

1. **Primary Request and Intent** — What the user originally asked for \
and any refinements.
2. **Key Technical Concepts** — Languages, frameworks, libraries, APIs, \
architecture decisions mentioned.
3. **Files and Code Sections** — Files read, written, or edited, with \
their purpose. Include paths.
4. **Errors and Fixes** — Errors encountered and how they were resolved.
5. **Problem Solving** — Approaches tried, what worked, what didn't.
6. **All User Messages** — Reproduce every user message verbatim (one \
bullet per message). Never merge or paraphrase user messages.
7. **Pending Tasks** — Work the assistant agreed to do but hasn't \
finished yet.
8. **Current Work** — What the assistant was doing right before this \
summary was requested.
9. **Optional Next Step** — What the assistant should do next, if clear."""

# Token warning thresholds — absolute buffers from context limit
# (more stable than percentages across different context window sizes)
AUTOCOMPACT_BUFFER = 13_000   # trigger autocompact at (max - 13K)
WARNING_BUFFER = 20_000       # approaching-limit warning at (max - 20K)
BLOCKING_BUFFER = 3_000       # hard blocking limit at (max - 3K)

# Snip: keep at least this many recent messages
SNIP_MIN_KEEP = 10

# Microcompact: truncate tool results longer than this
MICROCOMPACT_THRESHOLD = 50_000  # chars

# Microcompact: tools whose results are safe to aggressively clear
# (high output, low retention value after the agent has consumed them)
COMPACTABLE_TOOLS = {
    "bash", "file_read", "file_write", "file_edit",
    "grep", "glob", "web_search", "web_fetch",
}

# Microcompact: keep the N most recent compactable tool results untouched
MICROCOMPACT_KEEP_RECENT = 5

# Tool result budget: max aggregate chars across all tool results
TOOL_RESULT_BUDGET = 500_000  # chars

# Post-compact file restoration budget
FILE_RESTORE_MAX_TOKENS = 50_000
FILE_RESTORE_MAX_PER_FILE = 5_000
FILE_RESTORE_MAX_FILES = 5


def estimate_tokens(text: str) -> int:
    """Estimate token count. Delegates to token_estimation module."""
    from ..token_estimation import count_tokens
    return count_tokens(text)


def count_message_tokens(messages: list[Message]) -> int:
    """Estimate total tokens across all messages. Delegates to token_estimation module."""
    from ..token_estimation import count_messages_tokens
    return count_messages_tokens(messages)


class TokenWarningState:
    """Token usage warning levels using absolute buffer thresholds.

    For large context windows (>50K), uses absolute buffers.
    For small context windows, falls back to percentage-based thresholds
    to avoid negative thresholds.
    """

    def __init__(self, token_count: int, max_tokens: int):
        self.token_count = token_count
        self.max_tokens = max_tokens

    def _threshold(self, buffer: int, pct_fallback: float) -> int:
        """Compute threshold: absolute buffer if window is large enough, else percentage."""
        if self.max_tokens > buffer * 2:
            return self.max_tokens - buffer
        return int(self.max_tokens * pct_fallback)

    @property
    def is_approaching_limit(self) -> bool:
        return self.token_count >= self._threshold(WARNING_BUFFER, 0.75)

    @property
    def is_at_compact_threshold(self) -> bool:
        return self.token_count >= self._threshold(AUTOCOMPACT_BUFFER, 0.90)

    @property
    def is_at_blocking_limit(self) -> bool:
        return self.token_count >= self._threshold(BLOCKING_BUFFER, 0.95)


@dataclass
class CompactionResult:
    """Result of a compaction operation."""

    messages: list[Message]
    summary: str
    pre_compact_tokens: int
    post_compact_tokens: int
    boundary: CompactBoundaryMessage | None = None


@dataclass
class ContextManager:
    """Manages conversation context window with multi-layer compaction."""

    max_context_tokens: int = 128_000
    compact_threshold: float = 0.9  # kept for backward compat but buffer thresholds used
    _api_reported_tokens: int = 0
    _total_tool_result_chars: int = 0
    _read_file_state: dict[str, float] = field(default_factory=dict)  # path → mtime (for restoration)

    def update_usage(self, usage: Usage) -> None:
        """Update with API-reported token counts."""
        self._api_reported_tokens = usage.prompt_tokens

    def get_token_count(self, messages: list[Message]) -> int:
        """Get current token count. Prefers API-reported if available."""
        if self._api_reported_tokens > 0:
            return self._api_reported_tokens
        return count_message_tokens(messages)

    def get_warning_state(self, messages: list[Message]) -> TokenWarningState:
        """Get the current token warning state."""
        return TokenWarningState(
            self.get_token_count(messages), self.max_context_tokens
        )

    def needs_compaction(self, messages: list[Message]) -> bool:
        """Check if messages need compaction."""
        state = self.get_warning_state(messages)
        return state.is_at_compact_threshold

    # -- File state tracking (for post-compact restoration) --

    def track_file_read(self, path: str, mtime: float = 0.0) -> None:
        """Track a file that was read (for post-compact restoration)."""
        # Keep bounded — evict oldest when too many
        if len(self._read_file_state) >= FILE_RESTORE_MAX_FILES * 2:
            excess = len(self._read_file_state) - FILE_RESTORE_MAX_FILES
            for key in list(self._read_file_state.keys())[:excess]:
                del self._read_file_state[key]
        self._read_file_state[path] = mtime

    # -- Layer 1: Tool result budget --

    def apply_tool_result_budget(self, messages: list[Message]) -> list[Message]:
        """Cap aggregate tool result size across the conversation.

        Applied FIRST (before snip/microcompact) to prevent total overflow.
        """
        total_chars = 0
        result: list[Message] = []

        for msg in messages:
            if msg.role == "tool" and msg.content:
                total_chars += len(msg.content)
                if total_chars > TOOL_RESULT_BUDGET:
                    # Replace with truncated version
                    budget_left = max(0, TOOL_RESULT_BUDGET - (total_chars - len(msg.content)))
                    if budget_left > 0:
                        truncated = msg.content[:budget_left] + "\n... [budget exceeded, truncated]"
                    else:
                        truncated = "[Tool result omitted: aggregate budget exceeded]"
                    new_msg = Message(
                        role=msg.role,
                        content=truncated,
                        tool_call_id=msg.tool_call_id,
                        uuid=msg.uuid,
                        metadata={**msg.metadata, "budget_truncated": True},
                    )
                    result.append(new_msg)
                    continue
            result.append(msg)

        self._total_tool_result_chars = total_chars
        return result

    # -- Layer 2: Snip compaction --

    def snip_compact(self, messages: list[Message]) -> tuple[list[Message], int]:
        """Trim oldest non-system messages beyond context limit.

        Keeps system messages and the most recent SNIP_MIN_KEEP messages.
        Returns (trimmed_messages, tokens_freed).
        """
        if not self.needs_compaction(messages):
            return messages, 0

        system_msgs = [m for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]

        if len(non_system) <= SNIP_MIN_KEEP:
            return messages, 0

        # Keep the last SNIP_MIN_KEEP messages
        keep = non_system[-SNIP_MIN_KEEP:]
        removed = non_system[:-SNIP_MIN_KEEP]

        tokens_freed = sum(
            estimate_tokens(m.content or "") + 4 for m in removed
        )

        return system_msgs + keep, tokens_freed

    # -- Layer 3: Microcompact (tool-type-aware + size-based) --

    def microcompact(
        self,
        messages: list[Message],
        tools_by_name: dict[str, Any] | None = None,
    ) -> list[Message]:
        """Clear or truncate old tool results.

        Two strategies:
        1. **Tool-type-aware**: For compactable tools (bash, file_read, grep, glob,
           file_edit, file_write, web_search, web_fetch), keep the N most recent
           results intact and clear older ones entirely.
        2. **Size-based fallback**: Any tool result exceeding MICROCOMPACT_THRESHOLD
           is head+tail truncated regardless of tool type.
        """
        # Identify compactable tool result indices (by tool_call_id → tool name mapping)
        compactable_indices: list[int] = []
        for i, msg in enumerate(messages):
            if msg.role != "tool" or not msg.content:
                continue
            if msg.metadata.get("microcompacted"):
                continue
            tool_name = self._resolve_tool_name(msg, messages, tools_by_name)
            if tool_name in COMPACTABLE_TOOLS or (
                tools_by_name and tool_name in tools_by_name
                and getattr(tools_by_name[tool_name], "is_compactable", False)
            ):
                compactable_indices.append(i)

        # Keep the most recent N compactable results, clear the rest
        clear_set = set(compactable_indices[:-MICROCOMPACT_KEEP_RECENT]) if len(compactable_indices) > MICROCOMPACT_KEEP_RECENT else set()

        result: list[Message] = []
        for i, msg in enumerate(messages):
            if i in clear_set:
                result.append(Message(
                    role=msg.role,
                    content="[Old tool result content cleared]",
                    tool_call_id=msg.tool_call_id,
                    uuid=msg.uuid,
                    metadata={**msg.metadata, "microcompacted": True},
                ))
            elif msg.role == "tool" and msg.content and len(msg.content) > MICROCOMPACT_THRESHOLD:
                # Size-based fallback for any oversized result
                half = MICROCOMPACT_THRESHOLD // 2
                truncated = (
                    msg.content[:half]
                    + f"\n\n... [microcompact: truncated {len(msg.content) - MICROCOMPACT_THRESHOLD} chars] ...\n\n"
                    + msg.content[-half:]
                )
                result.append(Message(
                    role=msg.role, content=truncated, tool_call_id=msg.tool_call_id,
                    uuid=msg.uuid, metadata={**msg.metadata, "microcompacted": True},
                ))
            else:
                result.append(msg)
        return result

    @staticmethod
    def _resolve_tool_name(
        tool_msg: Message, messages: list[Message], tools_by_name: dict[str, Any] | None,
    ) -> str:
        """Resolve the tool name for a tool result message by finding its tool_call."""
        if not tool_msg.tool_call_id:
            return ""
        # Walk backwards to find the assistant message with matching tool_call
        for msg in reversed(messages):
            if msg.role == "assistant":
                for tc in msg.tool_calls:
                    if tc.id == tool_msg.tool_call_id:
                        return tc.function_name
        return ""

    # -- Layer 4: Autocompact (LLM summarization) --

    def compact_messages(
        self,
        messages: list[Message],
        summary: str,
    ) -> list[Message]:
        """Replace older messages with a summary + compact boundary message.

        Keeps: system message (first) + boundary + summary + recent messages (~25% of context).
        """
        if not messages:
            return messages

        pre_compact_tokens = self.get_token_count(messages)
        result: list[Message] = []

        # Keep system messages
        for msg in messages:
            if msg.role == "system" and not msg.metadata.get("is_compact_summary"):
                result.append(msg)
            else:
                break

        # Add compact boundary message
        boundary = CompactBoundaryMessage(
            summary=summary[:200],
            pre_compact_token_count=pre_compact_tokens,
        )
        result.append(
            Message(
                role="system",
                content=(
                    f"[Context was automatically compacted. "
                    f"Pre-compact tokens: {pre_compact_tokens:,}. "
                    f"Some earlier messages have been summarized below.]"
                ),
                is_meta=True,
                metadata={
                    "is_compact_boundary": True,
                    "pre_compact_tokens": pre_compact_tokens,
                    "boundary_uuid": boundary.uuid,
                },
            )
        )

        # Add compaction summary
        result.append(
            Message(
                role="system",
                content=f"[Conversation summary]\n{summary}",
                metadata={"is_compact_summary": True},
            )
        )

        # Keep recent messages (roughly last 25% of context)
        keep_tokens = int(self.max_context_tokens * 0.25)
        recent: list[Message] = []
        token_count = 0

        for msg in reversed(messages):
            if msg.role == "system" and not msg.metadata.get("is_compact_summary"):
                continue
            msg_tokens = estimate_tokens(msg.content or "")
            if token_count + msg_tokens > keep_tokens:
                break
            recent.append(msg)
            token_count += msg_tokens

        recent.reverse()
        result.extend(recent)
        return result

    def build_compact_prompt(self, messages: list[Message]) -> list[Message]:
        """Build a structured prompt to ask the LLM to summarize the conversation."""
        parts: list[str] = []
        for msg in messages:
            if msg.role == "system":
                continue
            prefix = msg.role.upper()
            content = msg.content or ""
            if msg.tool_calls:
                tool_names = [tc.function_name for tc in msg.tool_calls]
                content += f" [called tools: {', '.join(tool_names)}]"
            # Truncate very long tool results to keep compact prompt manageable
            if msg.role == "tool" and len(content) > 2000:
                content = content[:1000] + "\n...[truncated]...\n" + content[-500:]
            parts.append(f"{prefix}: {content}")

        conversation_text = "\n".join(parts)

        return [
            Message(role="system", content=COMPACT_SYSTEM_PROMPT),
            Message(
                role="user",
                content=f"Summarize this conversation:\n\n{conversation_text}",
            ),
        ]

    @staticmethod
    def extract_summary(raw: str) -> str:
        """Extract summary from <summary> tags if present, else return raw."""
        import re
        match = re.search(r"<summary>(.*?)</summary>", raw, re.DOTALL)
        return match.group(1).strip() if match else raw.strip()

    # -- Layer 5: Context Collapse (selective folding) --

    def context_collapse(
        self, messages: list[Message], max_collapse_ratio: float = 0.5
    ) -> tuple[list[Message], list[str]]:
        """Collapse older tool-heavy regions into summaries.

        Unlike autocompact (which replaces ALL old messages with one summary),
        context collapse selectively folds individual tool call/result pairs
        while keeping the conversation structure intact.

        Correctly handles interleaved tool results by matching tool_call_id.

        Returns (collapsed_messages, list_of_summaries_generated).
        """
        if not self.needs_compaction(messages):
            return messages, []

        # Protect the most recent 30% of messages from collapse
        system_msgs = [m for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]

        protect_count = max(4, int(len(non_system) * 0.3))
        if protect_count >= len(non_system):
            return messages, []

        collapsible = non_system[:-protect_count]
        protected = non_system[-protect_count:]

        # Build a set of tool_call_ids that have results in the collapsible region
        tool_result_ids_in_collapsible: set[str] = set()
        for msg in collapsible:
            if msg.role == "tool" and msg.tool_call_id:
                tool_result_ids_in_collapsible.add(msg.tool_call_id)

        # Identify assistant messages whose tool_calls are fully resolved
        # in the collapsible region (all results present)
        collapsible_assistant_uuids: set[str] = set()
        collapsible_tool_call_ids: set[str] = set()
        for msg in collapsible:
            if msg.role == "assistant" and msg.tool_calls:
                tc_ids = {tc.id for tc in msg.tool_calls}
                if tc_ids <= tool_result_ids_in_collapsible:
                    # All tool results are present — safe to collapse
                    collapsible_assistant_uuids.add(msg.uuid)
                    collapsible_tool_call_ids |= tc_ids

        result: list[Message] = []
        summaries: list[str] = []

        for msg in collapsible:
            if msg.uuid in collapsible_assistant_uuids:
                # Collapse the assistant message
                tool_names = [tc.function_name for tc in msg.tool_calls]
                summary = f"[Collapsed: called {', '.join(tool_names)}]"
                if msg.content:
                    summary += f" — {msg.content[:100]}"
                summaries.append(summary)
                result.append(Message(
                    role="assistant",
                    content=summary,
                    metadata={"collapsed": True, "original_tool_calls": len(msg.tool_calls)},
                ))
            elif msg.role == "tool" and msg.tool_call_id in collapsible_tool_call_ids:
                # Skip tool results that belong to collapsed assistant messages
                continue
            else:
                result.append(msg)

        return system_msgs + result + protected, summaries

    # -- Reactive Compact (triggered by API 413 prompt_too_long) --

    def reactive_compact(
        self, messages: list[Message]
    ) -> list[Message]:
        """Emergency compaction triggered by API prompt_too_long error.

        More aggressive than normal compaction — immediately applies all
        non-LLM layers. The caller should then retry the API call.
        If still too long, autocompact follows.

        Order: budget → snip → microcompact → collapse (mirrors Claude Code).
        """
        logger.warning("Reactive compact triggered (prompt too long)")

        # Apply all non-LLM layers in correct order
        result = self.apply_tool_result_budget(messages)

        result, freed = self.snip_compact(result)
        if freed > 0:
            logger.info("Reactive compact snipped %d tokens", freed)

        result = self.microcompact(result)

        # Apply context collapse (more aggressive than normal)
        result, summaries = self.context_collapse(result)
        if summaries:
            logger.info("Reactive compact collapsed %d regions", len(summaries))

        return result

    # -- Post-compact restoration (mirrors Claude Code) --

    def create_post_compact_attachments(
        self,
        agent_id: str | None = None,
        mcp_tool_names: list[str] | None = None,
        max_skill_tokens: int = 25_000,
        max_tokens_per_skill: int = 5_000,
    ) -> list[Message]:
        """Create attachment messages to restore context after compaction.

        Mirrors Claude Code's post-compact restoration:
        1. Re-inject recently read file contents (so LLM remembers key files)
        2. Re-inject invoked skill content (so LLM doesn't forget skill instructions)
        3. Re-announce MCP tool availability (so LLM knows MCP tools still exist)

        Returns list of system messages to append after compact boundary.
        """
        attachments: list[Message] = []

        # 1. File content re-injection (re-read from disk)
        if self._read_file_state:
            import os
            file_parts: list[str] = []
            total_tokens = 0
            # Take most recently tracked files
            recent_files = list(self._read_file_state.keys())[-FILE_RESTORE_MAX_FILES:]
            for path in recent_files:
                if not os.path.isfile(path):
                    continue
                try:
                    content = open(path, errors="replace").read()
                except OSError:
                    continue
                # Per-file token budget
                file_tokens = estimate_tokens(content)
                if file_tokens > FILE_RESTORE_MAX_PER_FILE:
                    char_limit = FILE_RESTORE_MAX_PER_FILE * 4
                    content = content[:char_limit] + "\n... [truncated for post-compact]"
                    file_tokens = FILE_RESTORE_MAX_PER_FILE
                if total_tokens + file_tokens > FILE_RESTORE_MAX_TOKENS:
                    break
                file_parts.append(f"### {path}\n```\n{content}\n```")
                total_tokens += file_tokens

            if file_parts:
                attachments.append(Message(
                    role="system",
                    content="[Recently read files — restored after context compaction]\n\n"
                    + "\n\n".join(file_parts),
                    is_meta=True,
                    metadata={"attachment_type": "read_files"},
                ))

        # 2. Skill re-injection
        try:
            from ...tools.SkillTool.skill_tool import get_invoked_skills
            invoked = get_invoked_skills(agent_id)
            if invoked:
                skill_parts = []
                total_tokens = 0
                for info in invoked:
                    content = info.content
                    # Per-skill token budget
                    if len(content) // 4 > max_tokens_per_skill:
                        content = content[: max_tokens_per_skill * 4]
                    tokens = len(content) // 4
                    if total_tokens + tokens > max_skill_tokens:
                        break
                    skill_parts.append(f"### {info.skill_name}\n{content}")
                    total_tokens += tokens

                if skill_parts:
                    attachments.append(Message(
                        role="system",
                        content="[Previously invoked skills — restored after context compaction]\n\n"
                        + "\n\n---\n\n".join(skill_parts),
                        is_meta=True,
                        metadata={"attachment_type": "invoked_skills"},
                    ))
        except ImportError:
            pass

        # 3. MCP tool re-announcement
        if mcp_tool_names:
            tool_list = "\n".join(f"- {name}" for name in mcp_tool_names)
            attachments.append(Message(
                role="system",
                content=f"[MCP tools available — restored after context compaction]\n\n"
                f"The following MCP tools are still available for use:\n{tool_list}",
                is_meta=True,
                metadata={"attachment_type": "mcp_instructions"},
            ))

        return attachments

    # -- Combined pipeline --

    def apply_all_layers(self, messages: list[Message]) -> list[Message]:
        """Apply all non-LLM compaction layers in order.

        Order: budget → snip → microcompact (mirrors Claude Code).
        Autocompact, context collapse, and reactive compact are separate
        (require LLM call or API error trigger).
        """
        result = self.apply_tool_result_budget(messages)
        result, _ = self.snip_compact(result)
        result = self.microcompact(result)
        return result
