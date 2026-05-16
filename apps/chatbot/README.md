# calcifer-chatbot

Browser-based chatbot consumer built on the Calcifer SDK.

It keeps the application layer deliberately thin:

- `Chatbot` wraps `calcifer.Agent` and preserves conversation state.
- `select_tools()` exposes `none`, `readonly`, and `all` tool modes. Readonly
  mode includes local read/search tools plus built-in `web_search`.
- A local standard-library web server renders the chat page.
- Each assistant answer keeps its own run details. Click the answer or its
  trace capsule to inspect that specific run.
- The inspector has Overview, Steps, Sources, and Raw views. Overview explains
  the run path, Steps groups model requests and tool activity into readable
  actions, Sources lifts web search results into clickable cards, and Raw keeps
  the full event payloads for debugging.
- The Stop button aborts the browser request and asks the current agent run to
  stop gracefully.
- Tests use `calcifer.testing.MockProvider`, so they run without a real LLM.

## Install

From the repository root:

```bash
pip install -e .
pip install -e apps/chatbot
```

## Run

```bash
# ~/.zshrc
export DEEPSEEK_API_KEY=sk-...

calcifer-chatbot
```

By default the chatbot uses DeepSeek's OpenAI-compatible API:

- base URL: `https://api.deepseek.com`
- model: `deepseek-v4-flash`

You can override either from the shell:

```bash
export DEEPSEEK_MODEL=deepseek-v4-flash
export DEEPSEEK_BASE_URL=https://api.deepseek.com
```

Useful options:

```bash
calcifer-chatbot --tools none
calcifer-chatbot --tools all
calcifer-chatbot --port 8766
calcifer-chatbot --no-open
calcifer-chatbot --provider openai --model gpt-4o-mini
```

## Test

```bash
pytest apps/chatbot/tests -q
```
