"""Tests for the chatbot web wrapper."""

from __future__ import annotations

import asyncio
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from calcifer import Agent, CalciferConfig, Message, StreamEvent, Usage
from calcifer.testing import MockProvider

from calcifer_chatbot.app import Chatbot
from calcifer_chatbot.web import ChatbotWebApp, _handler_for, _stream_event_payload, render_index_html


def _make_web_app(responses: list[str]) -> ChatbotWebApp:
    provider = MockProvider(responses=responses)
    agent = Agent(
        config=CalciferConfig(
            api_key="mock",
            base_url="mock",
            model="mock",
            system_prompt="You are a browser chatbot.",
        ),
        provider=provider,
    )
    return ChatbotWebApp(Chatbot(agent=agent))


class LoopStickyProvider:
    """Provider double that fails if requests hop between event loops."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.loop: asyncio.AbstractEventLoop | None = None

    def _assert_same_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self.loop is None:
            self.loop = loop
            return
        if self.loop is not loop:
            raise RuntimeError("Event loop changed")

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ) -> tuple[Message, Usage]:
        self._assert_same_loop()
        return Message(role="assistant", content=self.responses.pop(0)), Usage(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    async def chat_completion_stream(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ):
        self._assert_same_loop()
        yield StreamEvent(type="text_delta", text=self.responses.pop(0))
        yield StreamEvent(type="finish", finish_reason="stop")
        yield StreamEvent(
            type="usage",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


class PromptTooLongThenRecoverProvider:
    """Provider double for a streaming error followed by recovery retry."""

    def __init__(self) -> None:
        self.stream_calls = 0

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ) -> tuple[Message, Usage]:
        return Message(role="assistant", content="compact summary"), Usage(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    async def chat_completion_stream(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ):
        self.stream_calls += 1
        if self.stream_calls == 1:
            yield StreamEvent(type="text_delta", text="first answer")
            yield StreamEvent(type="finish", finish_reason="stop")
            yield StreamEvent(
                type="usage",
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
            return
        if self.stream_calls == 2:
            yield StreamEvent(
                type="error",
                error="prompt too long",
                error_code=400,
            )
            return
        yield StreamEvent(type="text_delta", text="weather answer")
        yield StreamEvent(type="finish", finish_reason="stop")
        yield StreamEvent(
            type="usage",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


def test_index_html_contains_chat_surface():
    html = render_index_html()

    assert '<div class="codex-app-shell">' in html
    assert '<aside class="workspace-rail">' not in html
    assert '<aside class="trace-panel">' not in html
    assert '<main class="codex-thread-shell">' in html
    assert '<div id="codex-workspace-grid" class="codex-workspace-grid">' in html
    assert '<aside id="agent-loop-panel" class="agent-loop-panel" hidden>' in html
    assert 'id="agent-loop-events"' in html
    assert 'id="agent-loop-detail"' in html
    assert 'Agent loop' in html
    assert 'class="message-list"' in html
    assert '.message[data-role="user"] {' in html
    assert 'align-self: flex-end;' in html
    assert '.message[data-role="assistant"] {' in html
    assert 'align-self: flex-start;' in html
    assert 'className = "loop-detail"' in html
    assert 'bindAssistantDetail(assistantView, payload);' in html
    assert 'renderAssistantTrace(assistantView);' in html
    assert 'assistantTrace.push(payload);' in html
    assert 'workspaceGrid.classList.add("has-trace");' in html
    assert 'workspaceGrid.classList.remove("has-trace");' in html
    assert 'id="notes-toggle"' in html
    assert 'id="raw-toggle"' in html
    assert 'id="stop-button"' in html
    assert 'fetch("/api/cancel"' in html
    assert 'function buildRunSummary(assistantView)' in html
    assert 'function buildInspectorModel(assistantView)' in html
    assert 'function renderInspector(model)' in html
    assert 'function renderInspectorDetail(node)' in html
    assert 'function formatStructuredValue(value)' in html
    assert 'className = "inspector-summary"' in html
    assert 'className = "timeline-node"' in html
    assert 'className = "timeline-child"' in html
    assert 'className = "timeline-note"' in html
    assert 'assistantView.node.addEventListener("click"' in html
    assert 'agentLoopDetail.hidden = true;' in html
    assert 'agentLoopDetail.hidden = false;' in html
    assert 'JSON.stringify(payload, null, 2)' in html
    assert 'selectTimelineNode(node, element)' in html
    assert 'Model request' in html
    assert 'Final answer' in html
    assert 'Verbose' not in html
    assert 'className = "loop-event"' not in html
    assert 'selectLoopEvent(payload, item)' not in html
    assert 'Assistant delta' not in html
    assert 'if (payload.type === "assistant_delta") {' in html
    assert 'return;\n        }\n        assistantTrace.push(payload);' in html
    assert 'className = "run-details"' not in html
    assert 'className = "run-summary"' not in html
    assert 'class="composer-card"' in html
    assert 'id="message-input"' in html
    assert 'fetch("/api/chat/stream"' in html
    assert 'white-space: normal;' in html
    assert 'flex: 0 0 auto;' in html
    assert 'class="status-pill metric-pill"' in html
    assert '.metric-pill {' in html
    assert "data-role" in html


def test_web_app_chat_updates_conversation():
    app = _make_web_app(["hello from web"])

    payload = app.chat("hello")

    assert payload["reply"] == "hello from web"
    assert payload["turns"] == 1
    assert payload["tokens"] == 2
    assert len(app.chatbot.conversation) == 3


def test_web_app_reset_clears_conversation():
    app = _make_web_app(["hello from web"])
    app.chat("hello")

    payload = app.reset()

    assert payload == {"ok": True}
    assert app.chatbot.conversation == []


def test_web_app_stream_chat_emits_trace_and_completion():
    app = _make_web_app(["streamed web answer"])
    emitted = []

    app.stream_chat("hello", emitted.append)

    assert emitted[0] == {
        "type": "run_start",
        "run_id": "run-1",
        "turn_id": 0,
        "input": "hello",
        "label": "Run 1",
    }
    assert emitted[1] == {
        "type": "trace",
        "stage": "turn_start",
        "label": "Turn 1 started",
        "run_id": "run-1",
        "turn_id": 1,
    }
    assert {
        "type": "assistant_delta",
        "text": "streamed web answer",
        "run_id": "run-1",
        "turn_id": 1,
    } in emitted
    assert any(
        event["type"] == "trace"
        and event["stage"] == "llm_finish"
        and event["detail"] == "stop"
        and event["run_id"] == "run-1"
        and event["turn_id"] == 1
        for event in emitted
    )
    assert any(
        event["type"] == "usage"
        and event["tokens"] == 2
        and event["run_id"] == "run-1"
        and event["turn_id"] == 1
        for event in emitted
    )
    complete = emitted[-1]
    assert complete["type"] == "complete"
    assert complete["run_id"] == "run-1"
    assert complete["turn_id"] == 1
    assert complete["reply"] == "streamed web answer"
    assert complete["turns"] == 1
    assert complete["cost_status"] == "unavailable"
    assert complete["summary"]["input"] == "hello"
    assert complete["summary"]["usage"]["total_tokens"] == 2
    assert complete["summary"]["finish_reason"] == "stop"


def test_web_app_stream_chat_reuses_one_event_loop_across_requests():
    provider = LoopStickyProvider(["first answer", "second answer"])
    agent = Agent(
        config=CalciferConfig(
            api_key="mock",
            base_url="mock",
            model="mock",
            system_prompt="You are a browser chatbot.",
        ),
        provider=provider,
    )
    app = ChatbotWebApp(Chatbot(agent=agent))
    first = []
    second = []

    app.stream_chat("first", first.append)
    app.stream_chat("second", second.append)

    assert first[-1]["type"] == "complete"
    assert first[-1]["reply"] == "first answer"
    assert second[-1]["type"] == "complete"
    assert second[-1]["reply"] == "second answer"


def test_web_app_stream_chat_retries_prompt_too_long_without_reusing_stale_reply():
    provider = PromptTooLongThenRecoverProvider()
    agent = Agent(
        config=CalciferConfig(
            api_key="mock",
            base_url="mock",
            model="mock",
            system_prompt="You are a browser chatbot.",
        ),
        provider=provider,
    )
    app = ChatbotWebApp(Chatbot(agent=agent))
    first = []
    second = []

    app.stream_chat("name", first.append)
    app.stream_chat("weather", second.append)

    assert first[-1]["type"] == "complete"
    assert first[-1]["reply"] == "first answer"
    assert second[-1]["type"] == "complete"
    assert second[-1]["reply"] == "weather answer"
    assert provider.stream_calls == 3


def test_stream_event_payload_structures_tool_arguments_and_results():
    start_payload = _stream_event_payload(
        StreamEvent(
            type="tool_call_start",
            turn=2,
            tool_call_id="tc_1",
            tool_call_name="grep",
            tool_call_arguments='{"pattern":"TODO","path":"README.md"}',
        ),
        run_id="run-7",
        turn_id=2,
    )
    result_payload = _stream_event_payload(
        StreamEvent(
            type="tool_call_result",
            tool_call_id="tc_1",
            tool_result_content='{"matches":3,"path":"README.md"}',
        ),
        run_id="run-7",
        turn_id=2,
    )

    assert start_payload == {
        "type": "trace",
        "stage": "tool_call",
        "label": "Tool: grep",
        "detail": '{"pattern":"TODO","path":"README.md"}',
        "run_id": "run-7",
        "turn_id": 2,
        "tool_call_id": "tc_1",
        "tool_name": "grep",
        "arguments": {"pattern": "TODO", "path": "README.md"},
    }
    assert result_payload == {
        "type": "trace",
        "stage": "tool_result",
        "label": "Tool result",
        "detail": '{"matches":3,"path":"README.md"}',
        "run_id": "run-7",
        "turn_id": 2,
        "tool_call_id": "tc_1",
        "result": {"matches": 3, "path": "README.md"},
        "is_error": False,
    }


def test_stream_event_payload_structures_tool_progress():
    payload = _stream_event_payload(
        StreamEvent(
            type="tool_progress",
            turn=3,
            tool_call_id="tc_search",
            tool_progress_type="search_results_received",
            tool_progress_message='Found 2 results for "example docs"',
            tool_progress_data={"query": "example docs", "result_count": 2},
        ),
        run_id="run-7",
        turn_id=3,
    )

    assert payload == {
        "type": "trace",
        "stage": "tool_progress",
        "label": "Found 2 results for \"example docs\"",
        "detail": '{"query": "example docs", "result_count": 2}',
        "run_id": "run-7",
        "turn_id": 3,
        "tool_call_id": "tc_search",
        "progress_type": "search_results_received",
        "progress": {"query": "example docs", "result_count": 2},
    }


def test_web_app_cancel_aborts_current_agent():
    app = _make_web_app(["unused"])

    payload = app.cancel()

    assert payload == {"ok": True}
    assert app.chatbot.agent._abort_event.is_set()


def test_stream_endpoint_closes_after_complete():
    app = _make_web_app(["streamed web answer"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)

    try:
        connection.request(
            "POST",
            "/api/chat/stream",
            body='{"message":"hello"}',
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()

        assert response.status == 200
        assert response.getheader("Connection") == "close"
        body = response.read().decode("utf-8")
        assert '"type": "complete"' in body
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
