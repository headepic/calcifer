"""End-to-end test with real LLM API.

Tests: agent loop, tool calling, streaming, cost tracking.
"""

import asyncio
from calcifer import Agent, CalciferConfig, tool


@tool(name="add", description="Add two numbers together")
def add(a: int, b: int) -> str:
    """Add two numbers and return the result."""
    return str(a + b)


@tool(name="multiply", description="Multiply two numbers together")
def multiply(a: int, b: int) -> str:
    """Multiply two numbers and return the result."""
    return str(a * b)


async def test_simple_text():
    """Test 1: Simple text response (no tools)."""
    print("\n=== Test 1: Simple text response ===")
    config = CalciferConfig(
        api_key="quotio-local-D4D439C0-3E09-47C5-8ABC-9B33F364B680",
        base_url="http://127.0.0.1:8317/v1",
        model="gpt-5.4-mini",
        system_prompt="You are a helpful assistant. Be concise.",
    )

    async with Agent(config=config) as agent:
        result = await agent.run("What is the capital of France? Answer in one word.")

    print(f"  Response: {result.final_text}")
    print(f"  Turns: {result.turn_count}")
    print(f"  Tokens: {result.usage.total_tokens}")
    assert result.final_text, "Expected non-empty response"
    assert result.turn_count == 1, "Expected 1 turn for simple question"
    print("  ✓ PASSED")


async def test_tool_calling():
    """Test 2: Tool calling loop."""
    print("\n=== Test 2: Tool calling ===")
    config = CalciferConfig(
        api_key="quotio-local-D4D439C0-3E09-47C5-8ABC-9B33F364B680",
        base_url="http://127.0.0.1:8317/v1",
        model="gpt-5.4-mini",
        system_prompt="You are a calculator. Use the provided tools. Be concise.",
    )

    async with Agent(config=config, tools=[add, multiply]) as agent:
        result = await agent.run("What is 7 + 3?")

    print(f"  Response: {result.final_text}")
    print(f"  Turns: {result.turn_count}")

    tool_msgs = [m for m in result.messages if m.role == "tool"]
    print(f"  Tool calls: {len(tool_msgs)}")
    for m in tool_msgs:
        print(f"    → {m.content}")

    assert result.turn_count >= 2, "Expected at least 2 turns (tool call + response)"
    assert any("10" in (m.content or "") for m in result.messages), "Expected 10 in some message"
    print("  ✓ PASSED")


async def test_multi_tool():
    """Test 3: Multiple tool calls in sequence."""
    print("\n=== Test 3: Multi-step tool calls ===")
    config = CalciferConfig(
        api_key="quotio-local-D4D439C0-3E09-47C5-8ABC-9B33F364B680",
        base_url="http://127.0.0.1:8317/v1",
        model="gpt-5.4-mini",
        system_prompt="You are a calculator. Use tools for every calculation. Be concise.",
    )

    async with Agent(config=config, tools=[add, multiply]) as agent:
        result = await agent.run("Calculate (3 + 4) * 5. First add 3+4, then multiply the result by 5.")

    print(f"  Response: {result.final_text}")
    print(f"  Turns: {result.turn_count}")

    tool_msgs = [m for m in result.messages if m.role == "tool"]
    print(f"  Tool calls: {len(tool_msgs)}")
    for m in tool_msgs:
        print(f"    → {m.content}")

    print(f"  Cost: ${agent.cost_tracker.get_cost():.6f}")
    print("  ✓ PASSED")


async def test_streaming():
    """Test 4: Streaming with lifecycle events."""
    print("\n=== Test 4: Streaming + lifecycle events ===")
    config = CalciferConfig(
        api_key="quotio-local-D4D439C0-3E09-47C5-8ABC-9B33F364B680",
        base_url="http://127.0.0.1:8317/v1",
        model="gpt-5.4-mini",
        system_prompt="You are a helpful assistant. Be concise.",
    )

    async with Agent(config=config, tools=[add]) as agent:
        chunks = []
        turns_started = []
        turns_ended = []
        tool_starts = []
        tool_results = []
        run_result = None

        async for event in agent.run_stream("What is 2 + 2? Use the add tool."):
            if event.type == "text_delta" and event.text:
                chunks.append(event.text)
                print(event.text, end="", flush=True)
            elif event.type == "turn_start":
                turns_started.append(event.turn)
                print(f"\n  [turn {event.turn} start]", end="", flush=True)
            elif event.type == "turn_end":
                turns_ended.append(event.turn)
            elif event.type == "tool_call_start":
                tool_starts.append(event.tool_call_name)
                print(f"\n  [tool: {event.tool_call_name}({event.tool_call_arguments})]", end="", flush=True)
            elif event.type == "tool_call_result":
                tool_results.append(event.tool_result_content)
                print(f" → {event.tool_result_content}", end="", flush=True)
            elif event.type == "run_complete":
                run_result = event.result
        print()

    text = "".join(chunks)
    print(f"  Streamed {len(chunks)} chunks, {len(text)} chars")
    print(f"  Turns: {turns_started} → {turns_ended}")
    print(f"  Tool calls: {tool_starts}")
    print(f"  Tool results: {tool_results}")

    assert chunks, "Expected streaming text output"
    assert len(turns_started) >= 2, f"Expected ≥2 turn_start events, got {len(turns_started)}"
    assert turns_started == turns_ended, "turn_start/turn_end mismatch"
    assert "add" in tool_starts, "Expected add tool_call_start"
    assert tool_results, "Expected tool_call_result events"
    assert run_result is not None, "Expected run_complete event"
    assert run_result.turn_count >= 2, "Expected ≥2 turns in result"
    print(f"  Final result: {run_result.turn_count} turns, {run_result.usage.total_tokens} tokens")
    print("  ✓ PASSED")


async def main():
    print("Calcifer Agent Runner — End-to-End Test")
    print("=" * 50)

    try:
        await test_simple_text()
        await test_tool_calling()
        await test_multi_tool()
        await test_streaming()
        print("\n" + "=" * 50)
        print("All tests PASSED ✓")
    except Exception as e:
        print(f"\n✗ FAILED: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
