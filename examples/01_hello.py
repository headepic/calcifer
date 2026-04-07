"""01 — Hello world.

最小可运行的 calcifer Agent：连一个 OpenAI 兼容 endpoint，问一句话，打印回复。

运行：
    OPENAI_API_KEY=... OPENAI_BASE_URL=... OPENAI_MODEL=... \\
        python examples/01_hello.py
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
    result = await agent.run("用一句话解释什么是 Python 的 GIL。")
    print(result.final_text)
    print(f"\n[turns={result.turn_count} usage={result.usage}]")


if __name__ == "__main__":
    asyncio.run(main())
