# Examples

可运行的端到端 cookbook 示例。

| 文件 | 说明 | 需要真实 LLM？ |
|---|---|---|
| `01_hello.py` | 最简单的 Agent 调用 | ✅ |
| `02_tool.py` | 自定义 `@tool` | ✅ |
| `03_stream.py` | `run_stream()` 流式输出 | ✅ |
| `04_testing.py` | `MockProvider` 离线测试 | ❌ |
| `05_mcp.py` | 接入 MCP server | ✅ + Node.js |
| `99_e2e_real_llm.py` | 综合 E2E 烟测试 | ✅ |

## 运行

需要真实 LLM 的示例读环境变量：

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1   # 可选
export OPENAI_MODEL=gpt-4o-mini                    # 可选
python examples/01_hello.py
```

接 Ollama / vLLM / 本地 endpoint：

```bash
export OPENAI_API_KEY=ollama
export OPENAI_BASE_URL=http://localhost:11434/v1
export OPENAI_MODEL=llama3
python examples/01_hello.py
```

接本地 quotio gateway（开发常用）：

```bash
export OPENAI_API_KEY=quotio-local-D4D439C0-3E09-47C5-8ABC-9B33F364B680
export OPENAI_BASE_URL=http://localhost:8317/v1
export OPENAI_MODEL=gpt-5.4-mini
python examples/01_hello.py
```

> **注意**：quotio 这个 endpoint 在非流式模式下会返回 `content: null`。
> calcifer 的 `LLMProvider` 已经内置 fallback：检测到这种空响应会自动
> 切到流式模式（一次性 sticky flag），后续调用直接走流式路径。第一次
> 调用会有一行 `WARNING`，之后就没有了。

`04_testing.py` 不需要任何环境变量，直接运行即可：

```bash
python examples/04_testing.py
```
