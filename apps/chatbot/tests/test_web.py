"""Tests for the chatbot web wrapper."""

from __future__ import annotations

import asyncio
import inspect
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from calcifer import Agent, CalciferConfig, Message, StreamEvent, Usage
from calcifer.testing import MockProvider

import calcifer_chatbot.web as web_module
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
    assert 'Run details' in html
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
    assert ".codex-workspace-grid.has-trace," in html
    assert 'id="overview-tab"' in html
    assert 'id="steps-tab"' in html
    assert 'id="sources-tab"' in html
    assert 'id="raw-tab"' in html
    assert 'id="stop-button"' in html
    assert 'fetch("/api/cancel"' in html
    assert 'function buildRunSummary(assistantView)' in html
    assert 'function buildRunDetailsModel(assistantView)' in html
    assert 'function buildTraceCapsule(summary)' in html
    assert 'function groupToolEventsByCall(children)' in html
    assert 'function renderOverview(model)' in html
    assert 'function renderSteps(model)' in html
    assert 'function buildAgentStepSequence(model)' in html
    assert 'function renderSources(model)' in html
    assert 'function renderRaw(model)' in html
    assert 'function renderInspector(model)' in html
    assert 'function renderInspectorDetail(node)' in html
    assert 'function formatStructuredValue(value)' in html
    assert 'function renderStructuredValue(value, options = {})' in html
    assert 'function structuredNodeSummary(value)' in html
    assert 'function renderStructuredSummary(key, value)' in html
    assert 'function appendJsonFieldRows(container, value)' in html
    assert 'className = "structured-json"' in html
    assert 'className = "json-toggle"' in html
    assert 'className = "json-toggle-summary"' in html
    assert 'toggle.open = depth < 2;' in html
    assert 'summary.append(renderStructuredSummary(key, item));' in html
    assert 'className = "json-field-row"' in html
    assert 'className = "tool-group-json"' in html
    assert '.loop-detail-payload .json-field-row {' in html
    assert '.tool-group-json .json-field-row {' in html
    assert 'grid-template-columns: minmax(120px, 38%) minmax(0, 1fr);' in html
    assert 'grid-template-columns: minmax(0, 1fr);' in html
    assert 'className = "trace-capsule"' in html
    assert 'className = "run-summary-card"' in html
    assert 'className = "trace-step"' in html
    assert 'className = "tool-group-card"' in html
    assert 'className = "source-card"' in html
    assert '.message[data-role="assistant"].is-pending .message-content' in html
    assert 'function setAssistantPlaceholder(assistantView, text)' in html
    assert 'function clearAssistantPlaceholder(assistantView)' in html
    assert 'function appendAssistantPathItem(assistantView, item)' in html
    assert 'function updateAssistantPathFromTrace(assistantView, payload)' in html
    assert 'setAssistantPlaceholder(assistantView, "Thinking...");' in html
    assert 'setAssistantPlaceholder(assistantView, "Searching web...");' in html
    assert 'appendAssistantPathItem(assistantView, {key: "input", label: "Understanding request"});' in html
    assert 'assistantView.pathItems = new Map();' in html
    assert 'className = "activity-path"' in html
    assert 'Searching web: ${query}' in html
    assert 'Found ${resultCount} results' in html
    assert 'assistantView.preserveActivityPath = true;' in html
    assert 'No response returned.' in html
    assert 'assistantView.node.addEventListener("click"' in html
    assert 'agentLoopDetail.hidden = true;' in html
    assert 'agentLoopDetail.hidden = false;' in html
    assert 'loopDetailPayload.replaceChildren(renderStructuredValue(payload));' in html
    assert 'JSON.stringify({summary: model.summary, trace: model.trace}, null, 2)' in html
    assert 'selectTraceNode(node, element)' in html
    assert 'Input' in html
    assert 'Thought' in html
    assert 'reasoning flow' in html
    assert 'LLM input' in html
    assert 'LLM output' in html
    assert 'Action' in html
    assert 'Observation' in html
    assert 'Response' in html
    assert 'Outcome' in html
    assert 'answer generated' in html
    assert 'function loopPurpose(turn)' in html
    assert 'function thoughtSummary(turn, lastTurn, model)' in html
    assert 'function buildThoughtStep(turn, lastTurn, model)' in html
    assert 'function buildActionStep(turn, group)' in html
    assert 'function buildObservationStep(turn, group)' in html
    assert 'function buildResponseStep(model)' in html
    assert 'function buildOutcomeNode(turn)' in html
    assert 'function messageRoleList(messages)' in html
    assert 'function toolResultSummary(content)' in html
    assert 'function toolNameList(tools)' in html
    assert 'function toolCallSummary(response)' in html
    assert 'function sourceListSummary(sources)' in html
    assert '.timeline-step[data-kind="action"],' in html
    assert '.timeline-step[data-kind="action"] + .timeline-step[data-kind="observation"]' in html
    assert '.timeline-step-detail > .tool-group-json .json-field-row' in html
    assert 'grid-template-columns: minmax(0, 1fr);' in html
    assert 'className = "timeline-turn-badge"' in html
    assert 'turnBadge.textContent = String(node.turn_id);' in html
    assert 'title.prepend(turnBadge);' in html
    assert 'if (node.turn_id) item.dataset.turnId = String(node.turn_id);' in html
    assert 'turn_id = null' in html
    assert 'if (node.tool_call_id) item.dataset.toolCallId = node.tool_call_id;' in html
    assert 'tool_call_id: group.tool_call_id,' in html
    assert 'tool_call_id = ""' in html
    assert 'return {id, kind, title, meta, preview, fields, detail, raw, status, children, tool_call_id, turn_id};' in html
    assert 'const thoughtSummaryText = thoughtSummary(turn, lastTurn, model);' in html
    assert 'preview: previewValue(thoughtSummaryText, 280)' in html
    assert 'search results observed' in html
    assert '`context: ${roles}`' in html
    assert '`latest: ${lastMessage}`' in html
    assert 'context: messageRoleList(detail.messages || [])' in html
    assert 'latest: lastMessageSummary(detail.messages || [])' in html
    assert 'tools_available' in html
    assert 'last_message' not in html
    assert '`messages: ${roles}`' not in html
    assert '`last: ${lastMessage}`' not in html
    assert 'assistant tool request' in html
    assert 'tool result: ${resultCount} result' in html
    assert 'content_summary' in html
    assert 'next_step' in html
    assert 'cited_links' in html
    assert 'function normalizeCitationUrl(value)' in html
    assert 'const markdownUrls = [];' in html
    assert 'contentWithoutMarkdown' in html
    assert 'function llmInputPreview(payload)' in html
    assert 'function llmOutputPreview(payload)' in html
    assert 'children: thoughtChildren' in html
    assert 'children = []' in html
    assert 'thoughtChildren.push(buildLlmInputNode(turn));' in html
    assert 'thoughtChildren.push(buildLlmOutputNode(turn));' in html
    assert 'thoughtChildren.push(buildOutcomeNode(turn));' in html
    assert 'Raw provider notes' not in html
    assert 'provider_note_count' not in html
    assert 'source: "derived from trace events"' not in html
    assert 'summary: thoughtSummaryText' not in html
    assert html.index('steps.push(buildThoughtStep(turn, lastTurn, model));') < html.index('steps.push(buildActionStep(turn, group));')
    assert html.index('steps.push(buildActionStep(turn, group));') < html.index('steps.push(buildObservationStep(turn, group));')
    assert 'function appendTimelineStep(container, node, defaultOpen = false)' in html
    assert 'function appendTimelineChildren(container, children)' in html
    assert 'function timelineRawInfoNode(node)' in html
    assert 'className = "timeline-info-button"' in html
    assert 'rawButton.textContent = "i";' in html
    assert 'rawButton.setAttribute("aria-label", `Show full ${node.title} payload`);' in html
    assert 'event.stopPropagation();' in html
    assert 'selectTraceNode(timelineRawInfoNode(node), rawButton);' in html
    assert 'className = "reasoning-timeline"' in html
    assert 'className = "timeline-step trace-step"' in html
    assert 'className = "timeline-step-detail"' in html
    assert 'appendJsonFieldRows(detail, node.fields);' in html
    assert '`${notes.length} note' not in html
    assert 'Understand request' not in html
    assert 'Decide next action' not in html
    assert 'Review web results' not in html
    assert 'Compose answer' not in html
    assert 'Model request' not in html
    assert 'Final answer' not in html
    assert 'Reasoning summary' not in html
    assert 'compactModelNotes(turn.notes)' not in html
    assert 'notesText || fallbackThought' not in html
    assert 'detail: {turn_id: turn.turn_id, notes: turn.notes}' not in html
    assert 'detail: model.inputNode.detail' not in html
    assert 'detail: group.call?.detail || group.detail' not in html
    assert 'detail: group.detail' not in html
    assert 'detail: model.finalNode.detail' not in html
    assert 'detail: turn.detail' not in html
    assert 'reasoning_content' not in html
    assert 'sources_included' not in html
    assert 'appendLoopCard(agentLoopEvents, step)' not in html
    assert 'Verbose' not in html
    assert 'className = "loop-event"' not in html
    assert 'selectLoopEvent(payload, item)' not in html
    assert 'Assistant delta' not in html
    assert 'if (payload.type === "assistant_delta") {' in html
    assert 'return;\n        }\n        assistantTrace.push(payload);' in html
    assert 'splice(callIndex + 1, 0, child)' not in html
    assert 'function insertTimelineChildAfterRelatedEvents(children, child, toolCallId)' not in html
    assert 'className = "run-details"' not in html
    assert 'className = "run-summary"' not in html
    assert 'class="composer-card"' in html
    assert 'id="message-input"' in html
    assert 'fetch("/api/chat/stream"' in html
    assert 'white-space: normal;' in html
    assert 'flex: 0 0 auto;' in html
    assert '<span class="status-pill">chatbot</span>' in html
    assert '<span class="status-pill">web</span>' not in html
    assert '<span class="status-pill">readonly</span>' not in html
    assert 'class="status-pill metric-pill"' in html
    assert '.metric-pill {' in html
    assert "data-role" in html


def test_index_html_can_show_selected_tool_mode():
    html = render_index_html(tool_mode="workspace")

    assert '<span class="status-pill">workspace</span>' in html
    assert '<span class="status-pill">chatbot</span>' not in html


def test_cli_tool_mode_choices_expose_chatbot_not_web():
    source = inspect.getsource(web_module.main)

    assert 'choices=["none", "chatbot", "workspace", "readonly", "all"]' in source
    assert '"web"' not in source


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
    assert any(
        event["type"] == "trace"
        and event["stage"] == "llm_input"
        and event["label"] == "LLM input"
        and event["detail"]["messages"][-1]["content"] == "hello"
        and event["detail"]["model"] == "mock"
        and event["run_id"] == "run-1"
        and event["turn_id"] == 1
        for event in emitted
    )
    assert {
        "type": "assistant_delta",
        "text": "streamed web answer",
        "run_id": "run-1",
        "turn_id": 1,
    } in emitted
    assert any(
        event["type"] == "trace"
        and event["stage"] == "llm_output"
        and event["label"] == "LLM output"
        and event["detail"]["response"]["content"] == "streamed web answer"
        and event["detail"]["response"]["role"] == "assistant"
        and event["run_id"] == "run-1"
        and event["turn_id"] == 1
        for event in emitted
    )
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
