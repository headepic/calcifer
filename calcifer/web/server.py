"""FastAPI server with SSE streaming for the Calcifer Web GUI.

Endpoints:
    GET  /           → Chat UI (embedded HTML)
    POST /api/chat   → SSE stream of agent events
    GET  /api/models → Current model info
    POST /api/abort  → Abort current run
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from ..agent import Agent
from ..config import CalciferConfig
from ..tool import Tool
from ..types.message import Message
from .ui import CHAT_HTML


def create_app(
    config: CalciferConfig,
    tools: list[Tool] | None = None,
) -> FastAPI:
    """Create a FastAPI app with chat endpoint."""

    # Shared state
    state: dict[str, Any] = {
        "agent": None,
        "conversation": [],
        "config": config,
        "tools": tools or [],
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        agent = Agent(config=config, tools=state["tools"])
        if config.mcp_servers:
            await agent.connect_mcp_servers()
        if config.skills_dirs:
            agent.load_skills()
        state["agent"] = agent
        yield
        if state["agent"]:
            await state["agent"].close()

    app = FastAPI(title="Calcifer", version="0.2.0", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return CHAT_HTML

    @app.post("/api/chat")
    async def chat(request: Request):
        body = await request.json()
        prompt = body.get("message", "").strip()
        if not prompt:
            return JSONResponse({"error": "Empty message"}, status_code=400)

        agent: Agent = state["agent"]
        conversation: list[Message] = state["conversation"]

        async def event_stream():
            nonlocal conversation
            async for event in agent.run_stream(
                prompt,
                messages=conversation if conversation else None,
            ):
                obj: dict[str, Any] = {"type": event.type}

                if event.type == "text_delta" and event.text:
                    obj["text"] = event.text
                elif event.type == "tool_call_start":
                    obj["tool_name"] = event.tool_call_name
                    obj["tool_args"] = event.tool_call_arguments
                elif event.type == "tool_call_result":
                    obj["tool_id"] = event.tool_call_id
                    obj["output"] = (event.tool_result_content or "")[:5000]
                    obj["is_error"] = event.tool_is_error
                elif event.type == "run_complete" and event.result:
                    conversation.clear()
                    conversation.extend(event.result.messages)
                    obj["final_text"] = event.result.final_text
                    obj["turn_count"] = event.result.turn_count
                    obj["tokens"] = event.result.usage.total_tokens
                    obj["cost"] = agent.cost_tracker.get_cost()
                elif event.type == "error":
                    obj["error"] = event.error
                else:
                    continue

                yield f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/abort")
    async def abort():
        agent: Agent = state["agent"]
        if agent:
            agent.abort()
        return JSONResponse({"status": "aborted"})

    @app.get("/api/status")
    async def status():
        agent: Agent = state["agent"]
        return JSONResponse({
            "model": config.model,
            "tools": [t.name for t in state["tools"]],
            "messages": len(state["conversation"]),
            "cost": agent.cost_tracker.get_cost() if agent else 0,
        })

    @app.post("/api/clear")
    async def clear():
        state["conversation"].clear()
        return JSONResponse({"status": "cleared"})

    return app


def run_server(
    config: CalciferConfig,
    tools: list[Tool] | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8422,
) -> None:
    """Run the web GUI server."""
    import uvicorn

    app = create_app(config, tools)
    print(f"🔥 Calcifer Web GUI: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
