"""05 — 接入 MCP server.

calcifer 支持 Model Context Protocol (modelcontextprotocol.io)，
可以把任意 MCP server 的工具暴露给 Agent。

支持的 transport：
    - stdio:     subprocess 启动 MCP server (最常用)
    - sse:       Server-Sent Events
    - http:      纯 HTTP
    - websocket: WebSocket

这个示例用 stdio 启动一个 filesystem MCP server。
你需要先 `pip install -e ".[mcp]"`，并且 `npx -y @modelcontextprotocol/server-filesystem`
能跑起来 (Node.js + npx)。

运行：
    OPENAI_API_KEY=... python examples/05_mcp.py
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from calcifer import Agent, CalciferConfig, MCPServerConfig


async def main() -> None:
    config = CalciferConfig(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        mcp_servers=[
            MCPServerConfig(
                name="fs",
                transport="stdio",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem", str(Path.cwd())],
            ),
        ],
    )
    agent = Agent(config=config)
    await agent.connect_mcp_servers()
    try:
        result = await agent.run(
            "列出当前目录下所有 .py 文件的名字（不含路径）。"
        )
        print(result.final_text)
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
