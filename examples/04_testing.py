"""04 — 用 MockProvider 离线测试 Agent.

calcifer.testing 提供 MockProvider 和 assertion helpers，
可以在没有真实 LLM 的情况下测试 Agent 行为：
    - 单元测试 / CI 不需要 API key
    - 完全确定性的回复
    - 可以校验工具是否被调用、调用了几次、参数是什么

这个文件本身就是一个可运行的测试 —— 不需要任何环境变量。
"""
from __future__ import annotations

import asyncio
import json

from calcifer import Agent, Message, ToolCall, tool
from calcifer.testing import MockProvider, assert_message_count, assert_tool_called


@tool(name="add", description="两数相加")
def add(a: int, b: int) -> str:
    return str(a + b)


async def test_basic_text_response() -> None:
    """场景 1：模型直接回文本，不调用工具。"""
    provider = MockProvider(responses=["Hello from mock!"])
    agent = Agent(
        provider=provider,
        api_key="x",
        base_url="x",
        model="mock",
    )
    result = await agent.run("Hi")
    assert result.final_text == "Hello from mock!"
    print("[1] basic text:", result.final_text)


async def test_tool_call_then_final() -> None:
    """场景 2：第一轮调用工具，第二轮把工具结果总结成最终答案。"""
    tool_call_msg = Message(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="c1", function_name="add", arguments=json.dumps({"a": 7, "b": 8}))],
    )
    provider = MockProvider(responses=[tool_call_msg, "答案是 15。"])
    agent = Agent(
        provider=provider,
        api_key="x",
        base_url="x",
        model="mock",
        tools=[add],
    )
    result = await agent.run("7 加 8 等于几？")

    assert result.final_text == "答案是 15。"
    assert_tool_called(result, "add", args_contains={"a": 7})
    print("[2] tool call: ok")


async def test_message_count_assertion() -> None:
    """场景 3：用 assert_message_count 校验消息数量。"""
    provider = MockProvider(responses=["ok"])
    agent = Agent(provider=provider, api_key="x", base_url="x", model="mock")
    result = await agent.run("hi")
    assert_message_count(result, count=1, role="assistant")
    print("[3] message count: ok")


async def main() -> None:
    await test_basic_text_response()
    await test_tool_call_then_final()
    await test_message_count_assertion()
    print("\nAll mock tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
