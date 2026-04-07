"""02 — 自定义 Tool.

用 @tool 装饰器把一个 Python 函数变成 Agent 可调用的工具。
Agent 会决定何时调用、传什么参数、把结果接回上下文继续。

运行：
    OPENAI_API_KEY=... python examples/02_tool.py
"""
from __future__ import annotations

import asyncio
import os

from calcifer import Agent, tool


@tool(name="add", description="把两个整数相加，返回字符串结果")
def add(a: int, b: int) -> str:
    return str(a + b)


@tool(name="multiply", description="把两个整数相乘")
def multiply(a: int, b: int) -> str:
    return str(a * b)


async def main() -> None:
    agent = Agent(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        tools=[add, multiply],
    )
    result = await agent.run("先算 7 加 8，再把结果乘以 3。最终答案是多少？")
    print(result.final_text)


if __name__ == "__main__":
    asyncio.run(main())
