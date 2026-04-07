"""End-to-end tests for TUI and Web GUI.

Tests:
- CLI argument parsing and mode selection
- Print mode: text, json, stream-json formats with real LLM
- Web GUI: FastAPI app creation, SSE chat endpoint, abort, status, clear
- TUI renderer: all rendering functions
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

from calcifer import Agent, CalciferConfig, Message, ToolCall, Usage, tool
from calcifer.tui.renderer import (
    render_assistant_text,
    render_compact_notification,
    render_spinner,
    render_status_bar,
    render_system_message,
    render_tool_call_start,
    render_tool_result,
    render_user_message,
    render_welcome,
)
from calcifer.tui.app import run_print_mode
from calcifer.tui.theme import CALCIFER_THEME

API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def check_llm():
    import httpx
    try:
        return httpx.get(f"{BASE_URL}/models", headers={"Authorization": f"Bearer {API_KEY}"}, timeout=3).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not check_llm(), reason="LLM not available")


@tool(name="add", description="Add two numbers")
def add_tool(a: int, b: int) -> str:
    return str(a + b)


# ===================================================================
# TUI Renderer Tests (unit, no LLM needed)
# ===================================================================

class TestRenderer:

    def test_render_user_message(self):
        result = render_user_message("Hello world")
        assert "Hello world" in str(result)
        assert "❯" in str(result)

    def test_render_assistant_text_streaming(self):
        result = render_assistant_text("partial text", streaming=True)
        assert "partial text" in str(result)

    def test_render_assistant_text_complete(self):
        result = render_assistant_text("# Heading\n\nParagraph with **bold**.", streaming=False)
        # Returns Markdown object for complete messages
        assert result is not None

    def test_render_tool_call_start_bash(self):
        result = render_tool_call_start("bash", '{"command": "echo hello"}')
        text = str(result)
        assert "bash" in text
        assert "echo hello" in text

    def test_render_tool_call_start_file_read(self):
        result = render_tool_call_start("file_read", '{"file_path": "/tmp/test.py"}')
        assert "/tmp/test.py" in str(result)

    def test_render_tool_call_start_grep(self):
        result = render_tool_call_start("grep", '{"pattern": "TODO"}')
        assert "TODO" in str(result)

    def test_render_tool_result_success(self):
        result = render_tool_result("hello world")
        text = str(result)
        assert "✓" in text
        assert "hello world" in text

    def test_render_tool_result_error(self):
        result = render_tool_result("command not found", is_error=True)
        text = str(result)
        assert "✗" in text
        assert "command not found" in text

    def test_render_tool_result_long_truncated(self):
        long_output = "\n".join(f"line {i}" for i in range(50))
        result = render_tool_result(long_output)
        text = str(result)
        assert "more lines" in text

    def test_render_system_message(self):
        result = render_system_message("Conversation cleared.")
        assert "ℹ" in str(result)

    def test_render_spinner(self):
        result = render_spinner(0.5)
        text = str(result)
        # Should have a spinner frame and a verb
        assert any(v in text for v in ["Thinking", "Reasoning", "Processing", "Analyzing",
                                        "Considering", "Working", "Evaluating"])

    def test_render_spinner_with_tool(self):
        result = render_spinner(1.0, tool_name="bash")
        assert "bash" in str(result)

    def test_render_status_bar(self):
        usage = Usage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        result = render_status_bar("gpt-4o", usage, 0.01, 3, "/home/user/project")
        text = str(result)
        # Grid table doesn't render to text easily, but should not crash
        assert result is not None

    def test_render_welcome(self):
        from io import StringIO
        from rich.console import Console
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        console.print(render_welcome("gpt-4o"))
        output = buf.getvalue()
        assert "Calcifer" in output
        assert "gpt-4o" in output

    def test_render_compact_notification(self):
        result = render_compact_notification(100000, 50000)
        text = str(result)
        assert "100,000" in text
        assert "50,000" in text


# ===================================================================
# Print Mode E2E (real LLM)
# ===================================================================

class TestPrintMode:

    @pytest.mark.asyncio
    async def test_print_text(self, capsys):
        config = CalciferConfig(
            api_key=API_KEY, base_url=BASE_URL, model=MODEL,
            system_prompt="Answer in one word only.",
        )
        await run_print_mode(config, "Capital of Japan?", output_format="text")
        captured = capsys.readouterr()
        assert "tokyo" in captured.out.lower()

    @pytest.mark.asyncio
    async def test_print_json(self, capsys):
        config = CalciferConfig(
            api_key=API_KEY, base_url=BASE_URL, model=MODEL,
            system_prompt="Answer in one word.",
        )
        await run_print_mode(config, "Capital of France?", output_format="json")
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "paris" in data["final_text"].lower()
        assert data["turn_count"] >= 1
        assert data["usage"]["total_tokens"] > 0

    @pytest.mark.asyncio
    async def test_print_stream_json(self, capsys):
        config = CalciferConfig(
            api_key=API_KEY, base_url=BASE_URL, model=MODEL,
            system_prompt="Answer briefly.",
        )
        await run_print_mode(config, "What is 1+1?", output_format="stream-json")
        captured = capsys.readouterr()
        lines = [l for l in captured.out.strip().split("\n") if l]
        assert len(lines) >= 2  # At least text_delta + run_complete
        events = [json.loads(l) for l in lines]
        types = {e["type"] for e in events}
        assert "text_delta" in types
        assert "run_complete" in types

    @pytest.mark.asyncio
    async def test_print_with_tools(self, capsys):
        config = CalciferConfig(
            api_key=API_KEY, base_url=BASE_URL, model=MODEL,
            system_prompt="Use tools for math. Be concise.",
        )
        await run_print_mode(config, "Use add to compute 7+8.", [add_tool], output_format="text")
        captured = capsys.readouterr()
        # Tool call shown on stderr
        assert "tool" in captured.err.lower() or "15" in captured.out


# ===================================================================
# Web GUI E2E (FastAPI TestClient)
# ===================================================================

class TestWebGUI:

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        config = CalciferConfig(
            api_key=API_KEY, base_url=BASE_URL, model=MODEL,
            system_prompt="Be concise.",
        )
        from calcifer.web.server import create_app
        app = create_app(config, [add_tool])
        with TestClient(app) as c:
            yield c

    def test_index_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Calcifer" in resp.text
        assert "text/html" in resp.headers["content-type"]

    def test_status_endpoint(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == MODEL
        assert "add" in data["tools"]

    def test_clear_endpoint(self, client):
        resp = client.post("/api/clear")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"

    def test_abort_endpoint(self, client):
        resp = client.post("/api/abort")
        assert resp.status_code == 200
        assert resp.json()["status"] == "aborted"

    def test_chat_empty_message(self, client):
        resp = client.post("/api/chat", json={"message": ""})
        assert resp.status_code == 400

    def test_chat_sse_streaming(self, client):
        """Chat endpoint returns SSE stream with text_delta and run_complete."""
        resp = client.post(
            "/api/chat",
            json={"message": "What is 2+2? Answer with just the number."},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        # Parse SSE events
        events = []
        for line in resp.text.split("\n"):
            if line.startswith("data: ") and line[6:] != "[DONE]":
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass

        types = {e["type"] for e in events}
        assert "text_delta" in types, f"Got types: {types}"
        assert "run_complete" in types

        # Check run_complete has expected fields
        complete = next(e for e in events if e["type"] == "run_complete")
        assert "final_text" in complete
        assert complete["turn_count"] >= 1

    def test_chat_with_tool_call(self, client):
        """Chat with tool calling produces tool_call_start and tool_call_result events."""
        resp = client.post(
            "/api/chat",
            json={"message": "Use the add tool to compute 10+20. Report the result."},
        )
        assert resp.status_code == 200

        events = []
        for line in resp.text.split("\n"):
            if line.startswith("data: ") and line[6:] != "[DONE]":
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass

        types = {e["type"] for e in events}
        assert "tool_call_start" in types, f"Got types: {types}"
        assert "tool_call_result" in types

        # Tool result should contain 30
        tool_results = [e for e in events if e["type"] == "tool_call_result"]
        assert any("30" in (e.get("output", "")) for e in tool_results)

    def test_chat_conversation_persistence(self, client):
        """Second message can reference first conversation."""
        # First message
        resp1 = client.post("/api/chat", json={"message": "My name is Alice."})
        assert resp1.status_code == 200

        # Second message should remember
        resp2 = client.post("/api/chat", json={"message": "What is my name?"})
        events = []
        for line in resp2.text.split("\n"):
            if line.startswith("data: ") and line[6:] != "[DONE]":
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
        complete = next((e for e in events if e["type"] == "run_complete"), None)
        assert complete is not None
        assert "alice" in complete["final_text"].lower()

    def test_html_has_key_elements(self, client):
        """The HTML page has all required UI elements."""
        resp = client.get("/")
        html = resp.text
        assert 'id="messages"' in html
        assert 'id="input"' in html
        assert 'id="send-btn"' in html
        assert '/api/chat' in html
        assert 'EventSource' in html or 'text/event-stream' in html or 'reader.read' in html


# ===================================================================
# CLI Tests (argument parsing)
# ===================================================================

class TestCLI:

    def test_help(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "calcifer", "--help"],
            capture_output=True, text=True, cwd="/Users/jowang/Documents/github/calcifer",
        )
        assert result.returncode == 0
        assert "Calcifer" in result.stdout
        assert "--web" in result.stdout
        assert "--print" in result.stdout
        assert "--model" in result.stdout

    def test_print_mode_requires_prompt(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "calcifer", "-p"],
            capture_output=True, text=True, cwd="/Users/jowang/Documents/github/calcifer",
        )
        assert result.returncode != 0
        assert "requires a prompt" in result.stderr.lower() or "error" in result.stderr.lower()
