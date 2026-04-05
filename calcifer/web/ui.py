"""Embedded HTML/CSS/JS for the Calcifer Web Chat UI.

Single-file SPA with:
- Streaming SSE message rendering
- Tool call visualization (name, args, spinner, result)
- Markdown rendering (via marked.js CDN)
- Syntax highlighting (via highlight.js CDN)
- Dark theme inspired by Claude Code
- Mobile-responsive layout
"""

CHAT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calcifer</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.1/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
:root {
  --bg: #1a1b26; --bg2: #24283b; --bg3: #414868;
  --fg: #c0caf5; --fg2: #a9b1d6; --dim: #565f89;
  --accent: #7aa2f7; --green: #9ece6a; --red: #f7768e;
  --yellow: #e0af68; --cyan: #7dcfff; --orange: #ff9e64;
  --border: #3b4261; --radius: 8px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--fg); font-family: 'SF Mono', 'Cascadia Code', 'JetBrains Mono', monospace; font-size: 14px; height: 100vh; display: flex; flex-direction: column; }

/* Header */
.header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 12px 20px; display: flex; align-items: center; gap: 12px; }
.header .logo { font-size: 20px; }
.header .title { font-weight: 700; color: var(--fg); }
.header .model { color: var(--dim); font-size: 12px; }
.header .status { margin-left: auto; font-size: 12px; color: var(--dim); }

/* Messages */
.messages { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; scroll-behavior: smooth; }
.msg { max-width: 85%; line-height: 1.6; }
.msg.user { align-self: flex-end; background: var(--accent); color: #1a1b26; padding: 10px 16px; border-radius: var(--radius) var(--radius) 4px var(--radius); }
.msg.assistant { align-self: flex-start; background: var(--bg2); padding: 12px 16px; border-radius: var(--radius) var(--radius) var(--radius) 4px; border: 1px solid var(--border); max-width: 95%; }
.msg.assistant pre { background: var(--bg); padding: 12px; border-radius: 6px; overflow-x: auto; margin: 8px 0; }
.msg.assistant code { font-size: 13px; }
.msg.assistant p { margin: 4px 0; }
.msg.assistant ul, .msg.assistant ol { padding-left: 20px; margin: 4px 0; }

/* Tool calls */
.tool-call { align-self: flex-start; background: var(--bg2); border: 1px solid var(--border); border-left: 3px solid var(--cyan); border-radius: var(--radius); padding: 10px 14px; max-width: 95%; font-size: 13px; }
.tool-call .tool-header { color: var(--cyan); font-weight: 600; display: flex; align-items: center; gap: 6px; }
.tool-call .tool-header .spinner { animation: spin 1s linear infinite; display: inline-block; }
@keyframes spin { to { transform: rotate(360deg); } }
.tool-call .tool-args { color: var(--dim); font-size: 12px; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tool-call .tool-output { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); color: var(--fg2); white-space: pre-wrap; font-size: 12px; max-height: 200px; overflow-y: auto; }
.tool-call .tool-output.error { color: var(--red); }
.tool-call .tool-status { font-size: 11px; margin-top: 4px; }
.tool-call .tool-status.ok { color: var(--green); }
.tool-call .tool-status.err { color: var(--red); }

/* Input */
.input-area { background: var(--bg2); border-top: 1px solid var(--border); padding: 12px 20px; display: flex; gap: 10px; }
.input-area textarea { flex: 1; background: var(--bg); color: var(--fg); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 14px; font-family: inherit; font-size: 14px; resize: none; outline: none; min-height: 44px; max-height: 120px; }
.input-area textarea:focus { border-color: var(--accent); }
.input-area button { background: var(--accent); color: #1a1b26; border: none; border-radius: var(--radius); padding: 0 20px; font-weight: 700; cursor: pointer; font-family: inherit; font-size: 14px; }
.input-area button:hover { opacity: 0.9; }
.input-area button:disabled { opacity: 0.4; cursor: not-allowed; }
.input-area button.abort { background: var(--red); }

/* Status bar */
.status-bar { background: var(--bg); border-top: 1px solid var(--border); padding: 4px 20px; font-size: 11px; color: var(--dim); display: flex; gap: 16px; }
</style>
</head>
<body>

<div class="header">
  <span class="logo">🔥</span>
  <span class="title">Calcifer</span>
  <span class="model" id="model-name">loading...</span>
  <span class="status" id="header-status"></span>
</div>

<div class="messages" id="messages"></div>

<div class="input-area">
  <textarea id="input" placeholder="Type a message..." rows="1" autofocus></textarea>
  <button id="send-btn" onclick="sendMessage()">Send</button>
</div>

<div class="status-bar">
  <span id="status-tokens"></span>
  <span id="status-cost"></span>
  <span id="status-turns"></span>
</div>

<script>
marked.setOptions({ highlight: (code, lang) => {
  if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, {language: lang}).value;
  return hljs.highlightAuto(code).value;
}});

const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('send-btn');
let busy = false;
let currentAssistant = null;
let currentToolEl = null;
let abortCtrl = null;

// Auto-resize textarea
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});
inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

// Load status
fetch('/api/status').then(r => r.json()).then(s => {
  document.getElementById('model-name').textContent = s.model;
});

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addUserMsg(text) {
  const el = document.createElement('div');
  el.className = 'msg user';
  el.textContent = text;
  messagesEl.appendChild(el);
  scrollBottom();
}

function startAssistant() {
  currentAssistant = document.createElement('div');
  currentAssistant.className = 'msg assistant';
  currentAssistant._text = '';
  messagesEl.appendChild(currentAssistant);
  return currentAssistant;
}

function appendAssistantText(text) {
  if (!currentAssistant) startAssistant();
  currentAssistant._text += text;
  currentAssistant.innerHTML = marked.parse(currentAssistant._text);
  scrollBottom();
}

function startToolCall(name, args) {
  // Finalize any pending assistant text
  if (currentAssistant && currentAssistant._text) {
    currentAssistant.innerHTML = marked.parse(currentAssistant._text);
    currentAssistant = null;
  }
  const el = document.createElement('div');
  el.className = 'tool-call';
  let argsSummary = '';
  try {
    const a = JSON.parse(args || '{}');
    argsSummary = a.command || a.file_path || a.pattern || Object.values(a)[0] || '';
    if (typeof argsSummary === 'string' && argsSummary.length > 80) argsSummary = argsSummary.slice(0, 80) + '...';
  } catch(e) {}
  el.innerHTML = `<div class="tool-header"><span class="spinner">◠</span> ${name}</div>` +
    (argsSummary ? `<div class="tool-args">${escHtml(argsSummary)}</div>` : '');
  messagesEl.appendChild(el);
  currentToolEl = el;
  scrollBottom();
}

function finishToolCall(output, isError) {
  if (!currentToolEl) return;
  const header = currentToolEl.querySelector('.tool-header');
  header.innerHTML = (isError ? '✗' : '✓') + ' ' + header.textContent.replace('◠ ', '');
  if (output) {
    const outEl = document.createElement('div');
    outEl.className = 'tool-output' + (isError ? ' error' : '');
    const lines = output.split('\n');
    outEl.textContent = lines.length > 12 ? lines.slice(0, 10).join('\n') + '\n... (' + (lines.length-10) + ' more lines)' : output;
    currentToolEl.appendChild(outEl);
  }
  const status = document.createElement('div');
  status.className = 'tool-status ' + (isError ? 'err' : 'ok');
  status.textContent = isError ? 'Failed' : 'Done';
  currentToolEl.appendChild(status);
  currentToolEl = null;
  scrollBottom();
}

function escHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || busy) return;

  // Handle /clear command
  if (text === '/clear') {
    messagesEl.innerHTML = '';
    inputEl.value = '';
    await fetch('/api/clear', {method:'POST'});
    return;
  }

  busy = true;
  sendBtn.textContent = 'Stop';
  sendBtn.className = 'abort';
  sendBtn.onclick = () => { fetch('/api/abort', {method:'POST'}); };
  inputEl.value = '';
  inputEl.style.height = 'auto';
  addUserMsg(text);
  currentAssistant = null;
  currentToolEl = null;

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text}),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});

      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data === '[DONE]') break;

        try {
          const evt = JSON.parse(data);
          if (evt.type === 'text_delta') appendAssistantText(evt.text);
          else if (evt.type === 'tool_call_start') startToolCall(evt.tool_name, evt.tool_args);
          else if (evt.type === 'tool_call_result') finishToolCall(evt.output, evt.is_error);
          else if (evt.type === 'run_complete') {
            document.getElementById('status-tokens').textContent = '↓' + (evt.tokens||0) + ' tokens';
            document.getElementById('status-cost').textContent = '$' + (evt.cost||0).toFixed(4);
            document.getElementById('status-turns').textContent = 'T' + (evt.turn_count||0);
          }
          else if (evt.type === 'error') appendAssistantText('\n\n**Error:** ' + evt.error);
        } catch(e) {}
      }
    }
  } catch(e) {
    appendAssistantText('\n\n**Connection error:** ' + e.message);
  }

  // Finalize
  if (currentAssistant && currentAssistant._text) {
    currentAssistant.innerHTML = marked.parse(currentAssistant._text);
  }
  currentAssistant = null;
  busy = false;
  sendBtn.textContent = 'Send';
  sendBtn.className = '';
  sendBtn.onclick = sendMessage;
  inputEl.focus();
}
</script>
</body>
</html>
"""
