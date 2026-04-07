"""03 — 流式输出.

用 run_stream() 实时接收 token 增量。事件类型包括：
    - text_delta:    模型输出的文本片段
    - tool_call_delta: 工具参数的增量（流式工具调用）
    - tool_result:   工具执行结果
    - finish:        本轮完成
    - usage:         token 用量统计

运行：
    OPENAI_API_KEY=... python examples/03_stream.py
"""
from __future__ import annotations

import asyncio
import os

from calcifer import Agent


async def main() -> None:
    agent = Agent(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    )
    async for event in agent.run_stream("写一首关于火焰的四行短诗。"):
        if event.type == "text_delta":
            print(event.text, end="", flush=True)
        elif event.type == "finish":
            print()  # newline


if __name__ == "__main__":
    asyncio.run(main())
