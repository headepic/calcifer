"""Tests for the built-in web_search tool."""

from __future__ import annotations

import json

import pytest

from calcifer import Agent, CalciferConfig
from calcifer.services.tools.orchestrator import execute_tool_call
from calcifer.tool_registry import get_all_builtin_tools
from calcifer.tools import WebSearchTool
from calcifer.testing import MockProvider
from calcifer.tools.WebSearchTool.web_search_tool import SearchResult
from calcifer.types.message import ToolCall
from calcifer.types.tools import ToolContext


class FakeSearchBackend:
    def __init__(
        self,
        results: list[SearchResult] | None = None,
        error: Exception | None = None,
    ):
        self.results = results or []
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        user_location: str = "",
    ) -> list[SearchResult]:
        self.calls.append(
            {
                "query": query,
                "max_results": max_results,
                "user_location": user_location,
            }
        )
        if self.error:
            raise self.error
        return self.results[:max_results]


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext()


def test_web_search_is_registered_and_safe():
    tool = WebSearchTool(backend=FakeSearchBackend())

    assert tool.name == "web_search"
    assert tool.is_read_only is True
    assert tool.is_concurrency_safe is True
    assert tool.is_compactable is True

    builtin_names = {tool.name for tool in get_all_builtin_tools()}
    assert "web_search" in builtin_names


def test_web_search_schema_exposes_agent_friendly_inputs():
    schema = WebSearchTool(backend=FakeSearchBackend()).to_openai_schema()
    params = schema["function"]["parameters"]

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "web_search"
    assert params["required"] == ["query"]
    assert params["properties"]["max_results"]["minimum"] == 1
    assert params["properties"]["max_results"]["maximum"] == 10
    assert "allowed_domains" in params["properties"]
    assert "blocked_domains" in params["properties"]
    assert "user_location" in params["properties"]


@pytest.mark.asyncio
async def test_web_search_returns_compact_json_sources(ctx):
    backend = FakeSearchBackend(
        [
            SearchResult(
                title="Python 3.14 docs",
                url="https://docs.python.org/3.14/",
                snippet="Official Python documentation.",
            ),
            SearchResult(
                title="PEP index",
                url="https://peps.python.org/",
                snippet="Python Enhancement Proposals.",
            ),
        ]
    )
    tool = WebSearchTool(backend=backend)

    args = tool.validate_input(
        {
            "query": "python 3.14 docs",
            "max_results": 2,
            "user_location": "Singapore",
        }
    )
    result = await tool.call(args, ctx)

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["query"] == "python 3.14 docs"
    assert payload["search_count"] == 1
    assert payload["result_count"] == 2
    assert isinstance(payload["duration_seconds"], float)
    assert payload["duration_seconds"] >= 0
    assert payload["results"] == [
        {
            "title": "Python 3.14 docs",
            "url": "https://docs.python.org/3.14/",
            "snippet": "Official Python documentation.",
        },
        {
            "title": "PEP index",
            "url": "https://peps.python.org/",
            "snippet": "Python Enhancement Proposals.",
        },
    ]
    assert "sources" in payload["message"].lower()
    assert backend.calls == [
        {
            "query": "python 3.14 docs",
            "max_results": 2,
            "user_location": "Singapore",
        }
    ]


@pytest.mark.asyncio
async def test_web_search_filters_allowed_and_blocked_domains(ctx):
    backend = FakeSearchBackend(
        [
            SearchResult(
                title="Docs",
                url="https://docs.python.org/3/library/asyncio.html",
                snippet="Allowed subdomain result.",
            ),
            SearchResult(
                title="Blog",
                url="https://blog.python.org/2026/example",
                snippet="Blocked subdomain result.",
            ),
            SearchResult(
                title="Other",
                url="https://example.com/python",
                snippet="Outside allowed domain.",
            ),
        ]
    )
    tool = WebSearchTool(backend=backend)

    args = tool.validate_input(
        {
            "query": "python asyncio",
            "allowed_domains": ["python.org"],
            "blocked_domains": ["blog.python.org"],
        }
    )
    result = await tool.call(args, ctx)

    assert result.is_error is False
    payload = json.loads(result.content)
    assert [item["url"] for item in payload["results"]] == [
        "https://docs.python.org/3/library/asyncio.html"
    ]


@pytest.mark.asyncio
async def test_web_search_reports_empty_results_clearly(ctx):
    tool = WebSearchTool(backend=FakeSearchBackend())

    args = tool.validate_input({"query": "site with no matches"})
    result = await tool.call(args, ctx)

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["results"] == []
    assert payload["search_count"] == 1
    assert payload["result_count"] == 0
    assert "no web search results" in payload["message"].lower()


@pytest.mark.asyncio
async def test_web_search_empty_query_returns_invalid_input(ctx):
    tool = WebSearchTool(backend=FakeSearchBackend())

    args = tool.validate_input({"query": "   "})
    result = await tool.call(args, ctx)

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error_code"] == "invalid_input"


@pytest.mark.asyncio
async def test_web_search_too_short_query_returns_invalid_input(ctx):
    tool = WebSearchTool(backend=FakeSearchBackend())

    args = tool.validate_input({"query": "x"})
    result = await tool.call(args, ctx)

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error_code"] == "invalid_input"


@pytest.mark.asyncio
async def test_web_search_long_query_returns_query_too_long(ctx):
    tool = WebSearchTool(backend=FakeSearchBackend())

    args = tool.validate_input({"query": "x" * 501})
    result = await tool.call(args, ctx)

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error_code"] == "query_too_long"


@pytest.mark.asyncio
async def test_web_search_backend_error_returns_unavailable(ctx):
    tool = WebSearchTool(backend=FakeSearchBackend(error=RuntimeError("network down")))

    args = tool.validate_input({"query": "latest calcifer"})
    result = await tool.call(args, ctx)

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error_code"] == "unavailable"
    assert result.metadata["error_code"] == "unavailable"


@pytest.mark.asyncio
async def test_web_search_execution_marks_tool_errors_for_agent_traces(ctx):
    tool = WebSearchTool(backend=FakeSearchBackend(error=RuntimeError("network down")))

    result = await execute_tool_call(
        ToolCall(
            id="tc_search",
            function_name="web_search",
            arguments='{"query":"latest calcifer"}',
        ),
        {"web_search": tool},
        ctx,
    )

    assert result.metadata["is_error"] is True
    assert result.metadata["error_code"] == "unavailable"


@pytest.mark.asyncio
async def test_web_search_execution_binds_progress_to_tool_call_id(ctx):
    backend = FakeSearchBackend(
        [
            SearchResult(
                title="Docs",
                url="https://docs.example.com/",
                snippet="Documentation result.",
            )
        ]
    )
    tool = WebSearchTool(backend=backend)
    progress = []

    result = await execute_tool_call(
        ToolCall(
            id="tc_search",
            function_name="web_search",
            arguments='{"query":"example docs"}',
        ),
        {"web_search": tool},
        ctx,
        on_progress=progress.append,
    )

    assert result.metadata["is_error"] is False
    assert [event.tool_use_id for event in progress] == ["tc_search", "tc_search"]


@pytest.mark.asyncio
async def test_web_search_agent_stream_emits_tool_progress():
    provider = MockProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "tc_search",
                        "name": "web_search",
                        "arguments": {"query": "example docs"},
                    }
                ]
            },
            "Final answer.",
        ]
    )
    backend = FakeSearchBackend(
        [
            SearchResult(
                title="Docs",
                url="https://docs.example.com/",
                snippet="Documentation result.",
            )
        ]
    )
    agent = Agent(
        config=CalciferConfig(api_key="mock", base_url="mock", model="mock"),
        provider=provider,
        tools=[WebSearchTool(backend=backend)],
    )

    events = [event async for event in agent.run_stream("search docs")]
    progress_events = [event for event in events if event.type == "tool_progress"]

    assert [event.tool_progress_type for event in progress_events] == [
        "query_update",
        "search_results_received",
    ]
    assert [event.tool_call_id for event in progress_events] == [
        "tc_search",
        "tc_search",
    ]
    assert progress_events[0].tool_progress_data == {"query": "example docs"}
    assert progress_events[1].tool_progress_data == {
        "query": "example docs",
        "result_count": 1,
    }


@pytest.mark.asyncio
async def test_web_search_emits_query_and_result_progress(ctx):
    backend = FakeSearchBackend(
        [
            SearchResult(
                title="Docs",
                url="https://docs.example.com/",
                snippet="Documentation result.",
            )
        ]
    )
    tool = WebSearchTool(backend=backend)
    progress = []

    args = tool.validate_input({"query": "example docs"})
    result = await tool.call(args, ctx, on_progress=progress.append)

    assert result.is_error is False
    assert [event.type for event in progress] == [
        "query_update",
        "search_results_received",
    ]
    assert progress[0].data == {"query": "example docs"}
    assert progress[1].data == {"query": "example docs", "result_count": 1}
