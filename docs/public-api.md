# Calcifer Public API (v0.3.x)

This document is the canonical list of names Calcifer commits to keeping
stable per semver. **Anything not listed here is internal** and may
change in any release.

A snapshot test (`tests/test_packaging.py::test_public_api_surface`)
locks the surface — it fails on any drift in `calcifer.__all__` so
exports cannot be added or removed accidentally.

## Stability tiers

| Tier | Promise |
|---|---|
| **Stable** | Semver guaranteed. Breaking changes only in major versions (e.g. 0.x → 1.0). |
| **Provisional** | Exported but the API may change in any minor version (0.3 → 0.4). Use freely but expect to update on upgrade. |
| **Internal helpers** | Visible in `dir(calcifer)` but NOT in `__all__`. No stability guarantee. Avoid relying on. |

## Stable

These 20 names are the load-bearing public API. Changing them is a
breaking change.

### Core
- **`Agent`** — the main agent runner. Constructed with `api_key` /
  `base_url` / `model` / `tools`. Methods: `run()` (async),
  `run_sync()`, `run_stream()` (async iterator).
- **`AgentResult`** — dataclass returned by `Agent.run()`. Fields:
  `messages`, `final_text`, `usage`, `turn_count`.
- **`CalciferConfig`** — dataclass for Agent configuration. All
  config fields documented in the dataclass docstring.
- **`MCPServerConfig`** — dataclass describing one MCP server
  connection (name, transport, command/url, args, env).

### Tool API
- **`Tool`** — abstract base class for custom tools. Subclass and
  implement `call()`.
- **`FunctionTool`** — concrete `Tool` subclass produced by the
  `@tool` decorator.
- **`ToolContext`** — execution context passed to `Tool.call()`.
  Holds messages, file state, abort signal, hook manager.
- **`ToolResult`** — dataclass returned from `Tool.call()`. Fields:
  `content`, `is_error`, `metadata`.
- **`ValidationResult`** — dataclass returned from `Tool.check_input()`
  to validate arguments before execution.
- **`tool`** — decorator that turns a Python function into a Tool.
- **`find_tool_by_name`** — helper to look up a tool in a list by name.

### Tool registry
- **`get_all_builtin_tools`** — return the list of all built-in tools
  (BashTool, FileReadTool, FileWriteTool, FileEditTool, GlobTool,
  GrepTool, SkillTool, ToolSearchTool).
- **`get_tools`** — return enabled built-in tools.
- **`assemble_tool_pool`** — combine built-in + MCP tools, dedupe,
  and order for prompt cache stability.

### Messages
- **`Message`** — dataclass for chat messages. Role + content +
  tool_calls + metadata.
- **`ToolCall`** — dataclass for one tool invocation in an assistant
  message.
- **`Usage`** — token usage record returned by the LLM provider.
- **`StreamEvent`** — event type yielded by `Agent.run_stream()`.
  Discriminated by `type` field (`text_delta`, `tool_call_start`,
  `tool_call_result`, `turn_end`, `run_complete`, ...).
- **`APIErrorType`** — enum classifying LLM API errors
  (PROMPT_TOO_LONG, MAX_OUTPUT_TOKENS, OVERLOADED, ...).

### Errors
- **`LLMProviderError`** — base exception raised by the LLM provider
  layer. Carries `error_type` (APIErrorType).

### Settings
- **`load_settings`** — load `CalciferConfig` from a project YAML
  file with override merging.

## Provisional

These 8 names are exported but the underlying APIs are still
evolving. Treat each minor release as potentially-breaking for these.

### Multi-agent (`Coordinator`, `CoordinatorConfig`)
- **`Coordinator`** — orchestrates multiple worker agents in
  parallel with a shared scratchpad. The worker spawning + result
  aggregation contract may change as the multi-agent story matures.
- **`CoordinatorConfig`** — config for `Coordinator`. May gain
  fields.

### Context management (`ContextManager`)
- **`ContextManager`** — the 6-layer compaction pipeline. May gain
  knobs (microcompact intervals, snip aggressiveness, etc.) in
  minor releases. Most users use the default; only touch this if
  you need to tune compaction for unusual workloads.

### Hooks (`HookManager`, `HookConfig`, `HookEvent`)
- **`HookManager`** — registers shell-command and Python-callback
  hooks for tool execution events. **Note**: as of v0.3.0 the
  HookManager exists but is **not yet wired into the tool
  orchestrator** (see `wire-hooks-into-orchestrator` in the
  backlog). The API is stable in shape but the hook events
  currently don't fire from real tool execution.
- **`HookConfig`** — dataclass describing one hook (event,
  command/callback, tool pattern, timeout).
- **`HookEvent`** — enum of hook events (PreToolUse, PostToolUse,
  SessionStart, SessionEnd, UserPromptSubmit).

### LLM transport (`LLMProvider`)
- **`LLMProvider`** — the OpenAI-compatible HTTP transport class.
  Exposed for advanced users who want to swap in their own
  provider. The interface (chat_completion, chat_completion_stream)
  is not pinned and may evolve as we add features.

## Lower-priority (kept for v0.3.x but may move to internal)

These 3 names are public for now but are likely to be demoted to
internal helpers in v0.4.x. If you find yourself using them, please
file an issue describing your use case.

- **`CostTracker`** — tracks per-request cost from token usage
  records. Most users who care about cost should integrate
  OpenTelemetry directly.
- **`MetricsManager`** — internal telemetry helper.
- **`run_tools`** — low-level helper that runs a list of tool calls.
  Most users use `Agent` instead.

## Internal helpers

Anything visible in `dir(calcifer)` but **not** in `calcifer.__all__`
is internal. This includes (non-exhaustive):
- Submodules: `calcifer.services`, `calcifer.skills`,
  `calcifer.coordinator`, `calcifer.tui`, `calcifer.web`,
  `calcifer.utils`, `calcifer.tools`, `calcifer.types`,
  `calcifer.telemetry`, `calcifer.memdir`, `calcifer.tasks`
- Implementation details under those submodules

You can import from internal modules but Calcifer makes **no
stability guarantee** about them. Names may move, signatures may
change, behavior may shift.

## Semver policy

Calcifer follows [Semantic Versioning](https://semver.org/):

- **Major (1.0 → 2.0)**: removing or renaming any name in this
  document, or changing the type signature of any **Stable** name in
  a way that breaks existing code.
- **Minor (0.3 → 0.4)**: adding new names; changing **Provisional**
  names (with a CHANGELOG entry); deprecating Stable names with a
  warning that lasts at least one minor version.
- **Patch (0.3.0 → 0.3.1)**: bug fixes that don't change the
  documented behavior of any Stable or Provisional name.

## How to propose changes

If you want to add or remove a name from `__all__`:

1. Edit `calcifer/__init__.py` `__all__` list
2. Edit this document (`docs/public-api.md`) to match
3. Edit `tests/test_packaging.py` `_EXPECTED_PUBLIC_API` constant
4. The snapshot test `test_public_api_surface` will pass only if
   all three are in sync — that's intentional. Three coordinated
   edits = an intentional, reviewed change.
