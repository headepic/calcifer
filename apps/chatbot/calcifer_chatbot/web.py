"""Local web UI for the Calcifer chatbot."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .app import DEFAULT_SYSTEM_PROMPT, Chatbot, ProviderMode, ToolMode, build_chatbot


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def render_index_html() -> str:
    """Return the single-page chatbot UI."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Calcifer Chatbot</title>
  <style>
    :root {
      color-scheme: light;
      --page: #f7f7f4;
      --surface: #ffffff;
      --surface-soft: #f1f1ee;
      --surface-code: #eeeeea;
      --ink: #1f1f1d;
      --muted: #73736c;
      --muted-strong: #55554f;
      --line: #deded8;
      --line-strong: #c8c8c0;
      --accent: #0f766e;
      --accent-soft: #e4f3f0;
      --danger: #b42318;
      --shadow: 0 12px 34px rgba(31, 31, 29, 0.08);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--page);
      color: var(--ink);
    }
    button, textarea { font: inherit; }
    button { border-radius: 7px; }
    .codex-app-shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      background: var(--page);
    }
    .codex-thread-shell {
      min-width: 0;
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      background: var(--page);
    }
    .codex-workspace-grid {
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
    }
    .codex-workspace-grid.has-trace {
      grid-template-columns: minmax(0, 1fr) 360px;
    }
    .topbar {
      min-height: 54px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
    }
    .title-group {
      min-width: 0;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-mark {
      width: 26px;
      height: 26px;
      display: grid;
      place-items: center;
      border: 1px solid var(--line-strong);
      background: var(--surface-soft);
      color: var(--ink);
      font-size: 13px;
      font-weight: 750;
    }
    h1 {
      margin: 0;
      font-size: 15px;
      line-height: 1.2;
      font-weight: 700;
      letter-spacing: 0;
    }
    .workspace-path {
      min-width: 0;
      color: var(--muted);
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .run-strip {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .status-pill {
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 28px;
      padding: 5px 8px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--muted-strong);
      border-radius: 7px;
    }
    .status-dot {
      width: 7px;
      height: 7px;
      background: var(--accent);
      border-radius: 999px;
    }
    .reset-button {
      min-height: 28px;
      padding: 5px 9px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--ink);
      cursor: pointer;
    }
    .ghost-button {
      min-height: 26px;
      padding: 4px 8px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--muted-strong);
      cursor: pointer;
      font-size: 12px;
    }
    .thread-wrap {
      min-height: 0;
      overflow-y: auto;
      background: var(--page);
    }
    .agent-loop-panel {
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      border-left: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.82);
    }
    .agent-loop-panel[hidden] {
      display: none;
    }
    .agent-loop-head {
      min-height: 46px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
    }
    .agent-loop-title {
      font-size: 13px;
      line-height: 1.2;
      font-weight: 700;
    }
    .agent-loop-status {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .agent-loop-controls {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .agent-loop-toggle {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      cursor: pointer;
    }
    .agent-loop-events {
      min-height: 0;
      overflow-y: auto;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .inspector-summary {
      display: grid;
      gap: 4px;
      padding: 0 0 12px;
      border-bottom: 1px solid var(--line);
      color: var(--muted-strong);
      font-size: 12px;
      line-height: 1.45;
    }
    .inspector-summary-title {
      color: var(--ink);
      font-weight: 700;
    }
    .inspector-summary-meta {
      color: var(--muted);
    }
    .loop-timeline {
      position: relative;
      display: grid;
      gap: 7px;
      padding: 2px 0 2px 17px;
    }
    .loop-timeline::before {
      content: "";
      position: absolute;
      left: 6px;
      top: 8px;
      bottom: 8px;
      width: 1px;
      background: var(--line-strong);
    }
    .timeline-node,
    .timeline-child,
    .timeline-note {
      position: relative;
      min-width: 0;
      display: grid;
      gap: 5px;
      padding: 8px 9px;
      border: 1px solid var(--line);
      background: var(--surface);
      border-radius: 8px;
      cursor: pointer;
    }
    .timeline-child,
    .timeline-note {
      margin-left: 18px;
      padding: 7px 8px;
      background: rgba(247, 247, 244, 0.72);
    }
    .timeline-node::before,
    .timeline-child::before,
    .timeline-note::before {
      content: "";
      position: absolute;
      left: -15px;
      top: 14px;
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--surface);
      border: 1px solid var(--line-strong);
    }
    .timeline-child::before,
    .timeline-note::before {
      left: -33px;
      width: 6px;
      height: 6px;
    }
    .timeline-node.is-selected,
    .timeline-child.is-selected,
    .timeline-note.is-selected {
      border-color: var(--accent);
      box-shadow: 0 0 0 1px rgba(15, 118, 110, 0.12);
    }
    .timeline-node[data-kind="error"],
    .timeline-node[data-kind="stopped"],
    .timeline-child[data-kind="tool_result_error"] {
      border-color: #efb2ad;
    }
    .timeline-node[data-kind="error"]::before,
    .timeline-node[data-kind="stopped"]::before,
    .timeline-child[data-kind="tool_result_error"]::before {
      border-color: #d92d20;
      background: #fff6f5;
    }
    .timeline-node[data-kind="final"]::before {
      border-color: var(--accent);
      background: var(--accent-soft);
    }
    .timeline-node-head {
      min-width: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .timeline-node-title {
      min-width: 0;
      color: var(--ink);
      font-size: 12px;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .timeline-node-meta {
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 11px;
    }
    .timeline-node-preview {
      color: var(--muted-strong);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 11px;
      line-height: 1.42;
    }
    .loop-detail {
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      border-top: 1px solid var(--line);
      background: var(--surface);
    }
    .loop-detail[hidden] {
      display: none;
    }
    .loop-detail-title {
      min-height: 34px;
      display: flex;
      align-items: center;
      padding: 8px 12px;
      border-bottom: 1px solid var(--line);
      color: var(--ink);
      font-size: 12px;
      font-weight: 700;
    }
    .loop-detail-payload {
      margin: 0;
      min-height: 0;
      overflow: auto;
      padding: 10px 12px;
      color: var(--muted-strong);
      background: var(--surface-code);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 11px;
      line-height: 1.5;
    }
    .message-list {
      width: min(900px, 100%);
      min-height: 100%;
      margin: 0 auto;
      padding: 28px 18px 36px;
      display: flex;
      flex-direction: column;
      gap: 24px;
    }
    .message {
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 7px;
    }
    .message[data-role="user"] {
      align-self: flex-end;
      width: fit-content;
      max-width: min(72%, 680px);
    }
    .message[data-role="assistant"] {
      align-self: flex-start;
      width: min(82%, 760px);
    }
    .message[data-role="assistant"].has-detail .message-content {
      cursor: pointer;
    }
    .message[data-role="assistant"].has-detail .message-content:hover {
      color: #0f625d;
    }
    .message[data-role="error"] {
      align-self: flex-start;
      width: min(82%, 760px);
      color: var(--danger);
    }
    .message-meta {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.2;
    }
    .message[data-role="user"] .message-meta {
      justify-content: flex-end;
      padding-right: 4px;
    }
    .message[data-role="assistant"] .message-meta,
    .message[data-role="error"] .message-meta {
      justify-content: flex-start;
      padding-left: 2px;
    }
    .message-content {
      min-width: 0;
      line-height: 1.58;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 15px;
    }
    .message[data-role="user"] .message-content {
      padding: 10px 13px;
      border: 1px solid var(--line);
      background: var(--surface);
      border-radius: 16px 16px 4px 16px;
      box-shadow: 0 1px 0 rgba(31, 31, 29, 0.03);
    }
    .message[data-role="assistant"] .message-content {
      padding: 2px 0 0;
      background: transparent;
    }
    .message[data-role="error"] .message-content {
      padding: 10px 12px;
      border: 1px solid #efb2ad;
      background: #fff6f5;
      border-radius: 8px;
    }
    .composer-zone {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
      padding: 12px 0 18px;
    }
    .composer-card {
      width: min(900px, calc(100% - 36px));
      margin: 0 auto;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 10px;
      align-items: end;
      border: 1px solid var(--line-strong);
      background: var(--surface);
      border-radius: 10px;
      box-shadow: var(--shadow);
      padding: 9px;
    }
    textarea {
      width: 100%;
      min-height: 50px;
      max-height: 190px;
      resize: vertical;
      border: 0;
      outline: 0;
      padding: 8px 9px;
      color: var(--ink);
      background: transparent;
      font-size: 15px;
      line-height: 1.45;
    }
    .send-button {
      width: 74px;
      height: 38px;
      border: 1px solid var(--ink);
      background: var(--ink);
      color: white;
      font-weight: 650;
      cursor: pointer;
    }
    .stop-button {
      width: 74px;
      height: 38px;
      border: 1px solid var(--line-strong);
      background: var(--surface-soft);
      color: var(--ink);
      font-weight: 650;
      cursor: pointer;
    }
    button:disabled {
      cursor: wait;
      opacity: 0.62;
    }
    @media (max-width: 980px) {
      .codex-workspace-grid,
      .composer-zone {
        grid-template-columns: 1fr;
      }
      .agent-loop-panel {
        min-height: 260px;
        max-height: 46vh;
        border-left: 0;
        border-top: 1px solid var(--line);
      }
      .composer-card {
        width: calc(100% - 24px);
      }
    }
    @media (max-width: 760px) {
      .topbar {
        align-items: flex-start;
        flex-direction: column;
        gap: 8px;
        padding: 10px 12px;
      }
      .title-group {
        width: 100%;
      }
      .run-strip {
        width: 100%;
        justify-content: flex-start;
        flex-wrap: wrap;
        white-space: normal;
      }
      .message-list {
        padding: 20px 12px 28px;
        gap: 20px;
      }
      .message[data-role="user"],
      .message[data-role="assistant"],
      .message[data-role="error"] {
        max-width: 92%;
      }
      .message[data-role="assistant"],
      .message[data-role="error"] {
        width: 92%;
      }
      .composer-zone {
        padding: 10px 0 12px;
      }
    }
    @media (max-width: 520px) {
      .composer-card {
        grid-template-columns: 1fr;
      }
      .send-button {
        width: 100%;
      }
      .workspace-path {
        display: none;
      }
      .metric-pill {
        display: none;
      }
    }
  </style>
</head>
<body>
  <div class="codex-app-shell">
    <main class="codex-thread-shell">
      <header class="topbar">
        <div class="title-group">
          <div class="brand-mark">C</div>
          <h1>Calcifer</h1>
          <div class="workspace-path">/Users/jowang/Documents/github/calcifer</div>
        </div>
        <div class="run-strip">
          <span class="status-pill">deepseek-v4-flash</span>
          <span class="status-pill">readonly</span>
          <span class="status-pill"><span class="status-dot"></span><span id="status">Ready</span></span>
          <span class="status-pill metric-pill"><span id="turn-count">0</span> turns</span>
          <span class="status-pill metric-pill"><span id="token-count">0</span> tokens</span>
          <button id="reset-button" class="reset-button" type="button">Reset</button>
        </div>
      </header>
      <div id="codex-workspace-grid" class="codex-workspace-grid">
        <div id="thread-wrap" class="thread-wrap">
          <section id="messages" class="message-list" aria-live="polite"></section>
        </div>
        <aside id="agent-loop-panel" class="agent-loop-panel" hidden>
          <div class="agent-loop-head">
            <div class="agent-loop-title">Agent loop</div>
            <div class="agent-loop-status">
              <span id="loop-status">Idle</span>
              <label class="agent-loop-toggle"><input id="notes-toggle" type="checkbox">Notes</label>
              <label class="agent-loop-toggle"><input id="raw-toggle" type="checkbox">Raw</label>
              <button id="close-trace-button" class="ghost-button" type="button">Close</button>
            </div>
          </div>
          <section id="agent-loop-events" class="agent-loop-events" aria-live="polite"></section>
          <section id="agent-loop-detail" class="loop-detail" hidden>
            <div id="agent-loop-detail-title" class="loop-detail-title">Payload</div>
            <pre id="agent-loop-payload" class="loop-detail-payload">{}</pre>
          </section>
        </aside>
      </div>
      <section class="composer-zone">
        <form id="chat-form" class="composer-card">
          <textarea id="message-input" name="message" placeholder="Message Calcifer" autocomplete="off"></textarea>
          <button id="stop-button" class="stop-button" type="button" disabled>Stop</button>
          <button id="send-button" class="send-button" type="submit">Send</button>
        </form>
      </section>
    </main>
  </div>
  <script>
    const form = document.getElementById("chat-form");
    const input = document.getElementById("message-input");
    const messages = document.getElementById("messages");
    const threadWrap = document.getElementById("thread-wrap");
    const workspaceGrid = document.getElementById("codex-workspace-grid");
    const agentLoopPanel = document.getElementById("agent-loop-panel");
    const agentLoopEvents = document.getElementById("agent-loop-events");
    const agentLoopDetail = document.getElementById("agent-loop-detail");
    const loopDetailTitle = document.getElementById("agent-loop-detail-title");
    const loopDetailPayload = document.getElementById("agent-loop-payload");
    const loopStatus = document.getElementById("loop-status");
    const status = document.getElementById("status");
    const sendButton = document.getElementById("send-button");
    const resetButton = document.getElementById("reset-button");
    const closeTraceButton = document.getElementById("close-trace-button");
    const notesToggle = document.getElementById("notes-toggle");
    const rawToggle = document.getElementById("raw-toggle");
    const stopButton = document.getElementById("stop-button");
    const turnCount = document.getElementById("turn-count");
    const tokenCount = document.getElementById("token-count");
    let currentAbortController = null;
    let activeAssistantView = null;
    let selectedInspectorNode = null;

    function scrollToBottom() {
      threadWrap.scrollTop = threadWrap.scrollHeight;
    }

    function timeLabel() {
      return new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    }

    function appendMessage(role, text) {
      const node = document.createElement("article");
      node.className = "message";
      node.setAttribute("data-role", role);

      const meta = document.createElement("div");
      meta.className = "message-meta";
      const label = document.createElement("span");
      label.textContent = role === "user" ? "You" : role === "error" ? "Error" : "Calcifer";
      const time = document.createElement("span");
      time.textContent = timeLabel();
      meta.append(label, time);

      const content = document.createElement("div");
      content.className = "message-content";
      content.textContent = text;

      node.append(meta);
      node.append(content);
      messages.appendChild(node);
      scrollToBottom();
      return {node, content};
    }

    function formatStructuredValue(value) {
      if (value === null || value === undefined || value === "") return "";
      if (typeof value === "object") return JSON.stringify(value, null, 2);
      return String(value);
    }

    function previewValue(value, limit = 240) {
      const text = formatStructuredValue(value).trim();
      if (!text) return "";
      const compact = text.split("\\n").slice(0, 4).join("\\n");
      return compact.length > limit ? `${compact.slice(0, limit - 1)}...` : compact;
    }

    function shortId(value) {
      const text = String(value || "");
      if (!text) return "";
      return text.length > 8 ? text.slice(-8) : text;
    }

    function compactMeta(parts) {
      return parts.filter(Boolean).join(" · ");
    }

    function renderInspectorDetail(node) {
      if (!node) return;
      selectedInspectorNode = node;
      agentLoopDetail.hidden = false;
      agentLoopDetail.className = "loop-detail";
      loopDetailTitle.textContent = node.title || "Payload";
      const payload = rawToggle.checked ? (node.raw || node.payload || node.detail || node) : (node.detail || node.payload || node);
      loopDetailPayload.textContent = JSON.stringify(payload, null, 2);
    }

    function hideAssistantTrace() {
      workspaceGrid.classList.remove("has-trace");
      agentLoopPanel.hidden = true;
      agentLoopEvents.innerHTML = "";
      agentLoopDetail.hidden = true;
      loopDetailTitle.textContent = "Payload";
      loopDetailPayload.textContent = "{}";
      loopStatus.textContent = "Idle";
      activeAssistantView = null;
      selectedInspectorNode = null;
    }

    function buildRunSummary(assistantView) {
      const complete = assistantView.completePayload || {};
      const summary = complete.summary || {};
      return {
        type: "run_summary",
        label: "Run summary",
        run_id: complete.run_id || summary.run_id || "",
        turn_id: complete.turn_id || summary.turn_id || 0,
        input: summary.input || "",
        reply: complete.reply || summary.reply || "",
        turns: complete.turns || summary.turns || 0,
        finish_reason: summary.finish_reason || "",
        usage: summary.usage || {
          prompt_tokens: complete.prompt_tokens || 0,
          completion_tokens: complete.completion_tokens || 0,
          total_tokens: complete.tokens || 0
        },
        tool_calls: summary.tool_calls || [],
        tool_results: summary.tool_results || [],
        cost: complete.cost ?? summary.cost ?? 0,
        cost_status: complete.cost_status || summary.cost_status || "unavailable"
      };
    }

    function buildInspectorModel(assistantView) {
      const summary = buildRunSummary(assistantView);
      const trace = (assistantView.trace || []).filter((payload) => payload && payload.type !== "assistant_delta");
      const turns = new Map();
      const nodes = [];
      const tools = new Map();
      let finalNode = null;
      let terminalNode = null;

      function ensureTurn(turnId) {
        const resolved = Number(turnId || 1);
        if (!turns.has(resolved)) {
          const node = {
            id: `turn-${resolved}`,
            kind: "model",
            title: `Model request ${resolved}`,
            meta: `turn ${resolved}`,
            preview: "Waiting for model response",
            turn_id: resolved,
            finish_reason: "",
            usage: null,
            notes: [],
            children: [],
            raw_events: [],
            detail: {turn_id: resolved, finish_reason: "", usage: null, notes: []},
          };
          turns.set(resolved, node);
          nodes.push(node);
        }
        return turns.get(resolved);
      }

      const inputPayload = trace.find((payload) => payload.type === "input");
      nodes.push({
        id: "input",
        kind: "input",
        title: "User input",
        meta: "input",
        preview: previewValue(inputPayload?.detail || summary.input),
        detail: {run_id: summary.run_id, input: inputPayload?.detail || summary.input},
        raw: inputPayload || {type: "input", detail: summary.input},
      });

      for (const payload of trace) {
        if (payload.type === "input" || payload.type === "run_start") continue;
        if (payload.type === "usage") {
          const turn = ensureTurn(payload.turn_id || summary.turn_id || 1);
          turn.usage = {
            prompt_tokens: payload.prompt_tokens || 0,
            completion_tokens: payload.completion_tokens || 0,
            total_tokens: payload.tokens || 0,
          };
          turn.raw_events.push(payload);
          continue;
        }
        if (payload.type === "complete") {
          finalNode = {
            id: "final",
            kind: "final",
            title: "Final answer",
            meta: compactMeta([
              `${payload.turns ?? summary.turns ?? 0} turns`,
              `${payload.tokens ?? summary.usage.total_tokens ?? 0} tokens`,
              summary.finish_reason ? `finish ${summary.finish_reason}` : "",
              (payload.cost_status || summary.cost_status) === "unavailable" ? "cost unavailable" : `cost ${payload.cost ?? summary.cost}`,
            ]),
            preview: previewValue(payload.reply || summary.reply),
            detail: {
              run_id: payload.run_id || summary.run_id,
              reply: payload.reply || summary.reply,
              turns: payload.turns || summary.turns,
              usage: summary.usage,
              finish_reason: summary.finish_reason,
              cost: payload.cost ?? summary.cost,
              cost_status: payload.cost_status || summary.cost_status,
            },
            raw: payload,
          };
          continue;
        }
        if (payload.type === "error") {
          terminalNode = {
            id: "error",
            kind: "error",
            title: "Error",
            meta: compactMeta([payload.code ? `code ${payload.code}` : "", payload.turn_id ? `turn ${payload.turn_id}` : ""]),
            preview: payload.message || "Request failed",
            detail: payload,
            raw: payload,
          };
          continue;
        }
        if (payload.type === "cancelled") {
          terminalNode = {
            id: "stopped",
            kind: "stopped",
            title: "Stopped",
            meta: "cancelled",
            preview: payload.message || "Run stopped by user",
            detail: payload,
            raw: payload,
          };
          continue;
        }
        if (payload.type !== "trace") continue;

        const turn = ensureTurn(payload.turn_id || summary.turn_id || 1);
        turn.raw_events.push(payload);
        if (payload.stage === "model_note") {
          turn.notes.push(payload.detail || "");
          continue;
        }
        if (payload.stage === "llm_finish") {
          turn.finish_reason = payload.detail || "";
          continue;
        }
        if (payload.stage === "tool_call") {
          const child = {
            id: `tool-call-${payload.tool_call_id || turn.children.length}`,
            kind: "tool_call",
            title: `Tool: ${payload.tool_name || "unknown"}`,
            meta: compactMeta([`turn ${turn.turn_id}`, shortId(payload.tool_call_id)]),
            preview: previewValue(payload.arguments || payload.detail),
            detail: {
              turn_id: turn.turn_id,
              tool_name: payload.tool_name,
              tool_call_id: payload.tool_call_id,
              arguments: payload.arguments,
            },
            raw: payload,
          };
          turn.children.push(child);
          if (payload.tool_call_id) tools.set(payload.tool_call_id, {turn, call: child});
          continue;
        }
        if (payload.stage === "tool_progress") {
          const pair = tools.get(payload.tool_call_id);
          const progressTurn = pair?.turn || turn;
          const child = {
            id: `tool-progress-${payload.tool_call_id || progressTurn.children.length}-${progressTurn.children.length}`,
            kind: "tool_progress",
            title: payload.label || "Tool progress",
            meta: compactMeta([shortId(payload.tool_call_id), payload.progress_type || "progress"]),
            preview: previewValue(payload.progress || payload.detail),
            detail: {
              tool_call_id: payload.tool_call_id,
              progress_type: payload.progress_type,
              progress: payload.progress,
            },
            raw: payload,
          };
          progressTurn.children.push(child);
          continue;
        }
        if (payload.stage === "tool_result") {
          const pair = tools.get(payload.tool_call_id);
          const resultTurn = pair?.turn || turn;
          const child = {
            id: `tool-result-${payload.tool_call_id || resultTurn.children.length}`,
            kind: payload.is_error ? "tool_result_error" : "tool_result",
            title: payload.is_error ? "Result failed" : "Result",
            meta: compactMeta([shortId(payload.tool_call_id), payload.is_error ? "failed" : "success"]),
            preview: previewValue(payload.result || payload.detail),
            detail: {
              tool_call_id: payload.tool_call_id,
              is_error: Boolean(payload.is_error),
              result: payload.result,
            },
            raw: payload,
          };
          resultTurn.children.push(child);
          if (pair?.call) {
            const resultIndex = resultTurn.children.indexOf(child);
            const callIndex = resultTurn.children.indexOf(pair.call);
            if (callIndex >= 0 && resultIndex >= 0 && resultIndex !== callIndex + 1) {
              resultTurn.children.splice(resultIndex, 1);
              resultTurn.children.splice(callIndex + 1, 0, child);
            }
          }
        }
      }

      for (const turn of turns.values()) {
        const usage = turn.usage;
        turn.meta = compactMeta([
          `turn ${turn.turn_id}`,
          turn.finish_reason ? `finish ${turn.finish_reason}` : "",
          usage ? `${usage.total_tokens} tokens` : "",
          turn.children.length ? `${turn.children.length} events` : "",
        ]);
        turn.preview = compactMeta([
          turn.notes.length ? `${turn.notes.length} model notes` : "",
          turn.children.length ? `${turn.children.length} tool events` : "model response",
        ]);
        turn.detail = {
          turn_id: turn.turn_id,
          finish_reason: turn.finish_reason,
          usage,
          notes: turn.notes,
        };
      }

      if (terminalNode) nodes.push(terminalNode);
      if (finalNode) nodes.push(finalNode);
      const selected = terminalNode || finalNode || nodes[nodes.length - 1] || null;
      const statusLabel = terminalNode ? terminalNode.title : finalNode ? "Complete" : "Trace";
      return {summary, nodes, selected, statusLabel};
    }

    function selectTimelineNode(node, element) {
      agentLoopEvents.querySelectorAll(".timeline-node, .timeline-child, .timeline-note").forEach((event) => {
        event.classList.remove("is-selected");
      });
      if (element) element.classList.add("is-selected");
      renderInspectorDetail(node);
    }

    function appendInspectorSummary(model) {
      const summary = model.summary;
      const node = document.createElement("div");
      node.className = "inspector-summary";
      const title = document.createElement("div");
      title.className = "inspector-summary-title";
      title.textContent = `${summary.run_id || "run"} · ${model.statusLabel}`;
      const meta = document.createElement("div");
      meta.className = "inspector-summary-meta";
      meta.textContent = compactMeta([
        `${summary.turns || 0} turns`,
        `${summary.usage.total_tokens || 0} tokens`,
        summary.finish_reason ? `finish ${summary.finish_reason}` : "",
      ]);
      node.append(title, meta);
      agentLoopEvents.appendChild(node);
    }

    function appendTimelineItem(container, node, options = {}) {
      const item = document.createElement("div");
      if (node.kind === "note") item.className = "timeline-note";
      else if (options.child) item.className = "timeline-child";
      else item.className = "timeline-node";
      item.setAttribute("data-kind", node.kind);
      item.setAttribute("data-node-id", node.id);
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");
      const head = document.createElement("div");
      head.className = "timeline-node-head";
      const title = document.createElement("div");
      title.className = "timeline-node-title";
      title.textContent = node.title;
      const meta = document.createElement("div");
      meta.className = "timeline-node-meta";
      meta.textContent = node.meta || "";
      head.append(title, meta);
      item.append(head);
      if (node.preview) {
        const preview = document.createElement("div");
        preview.className = "timeline-node-preview";
        preview.textContent = node.preview;
        item.append(preview);
      }
      item.addEventListener("click", () => selectTimelineNode(node, item));
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectTimelineNode(node, item);
        }
      });
      container.appendChild(item);
      return item;
    }

    function renderInspector(model) {
      agentLoopEvents.innerHTML = "";
      appendInspectorSummary(model);
      const timeline = document.createElement("div");
      timeline.className = "loop-timeline";
      for (const node of model.nodes) {
        appendTimelineItem(timeline, node);
        if (node.kind === "model" && notesToggle.checked) {
          node.notes.forEach((note, index) => {
            appendTimelineItem(timeline, {
              id: `${node.id}-note-${index}`,
              kind: "note",
              title: "Model note",
              meta: `turn ${node.turn_id}`,
              preview: previewValue(note, 180),
              detail: {turn_id: node.turn_id, note},
              raw: node.raw_events.find((event) => event.stage === "model_note") || node,
            }, {child: true});
          });
        }
        for (const child of node.children || []) {
          appendTimelineItem(timeline, child, {child: true});
        }
      }
      agentLoopEvents.appendChild(timeline);
      if (model.selected) {
        const selectedElement = agentLoopEvents.querySelector(`[data-node-id="${model.selected.id}"]`);
        selectTimelineNode(model.selected, selectedElement);
      } else {
        agentLoopDetail.hidden = true;
      }
    }

    function renderAssistantTrace(assistantView) {
      activeAssistantView = assistantView;
      workspaceGrid.classList.add("has-trace");
      agentLoopPanel.hidden = false;
      agentLoopDetail.hidden = true;
      const model = buildInspectorModel(assistantView);
      loopStatus.textContent = model.statusLabel;
      renderInspector(model);
    }

    function bindAssistantDetail(assistantView, payload) {
      assistantView.completePayload = payload;
      assistantView.node.classList.add("has-detail");
      assistantView.node.setAttribute("role", "button");
      assistantView.node.setAttribute("tabindex", "0");
      assistantView.node.addEventListener("click", () => renderAssistantTrace(assistantView));
      assistantView.node.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          renderAssistantTrace(assistantView);
        }
      });
    }

    function processSseChunk(chunk, onPayload) {
      const data = chunk
        .split("\\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\\n");
      if (!data) return;
      onPayload(JSON.parse(data));
    }

    async function sendMessage(text) {
      appendMessage("user", text);
      const assistantView = appendMessage("assistant", "");
      const assistantTrace = [{type: "input", label: "User input", detail: text}];
      assistantView.trace = assistantTrace;
      let assistantText = "";
      currentAbortController = new AbortController();
      sendButton.disabled = true;
      stopButton.disabled = false;
      status.textContent = "Running";
      loopStatus.textContent = "Running";

      function handlePayload(payload) {
        if (payload.type === "assistant_delta") {
          assistantText += payload.text || "";
          assistantView.content.textContent = assistantText;
          scrollToBottom();
          return;
        }
        assistantTrace.push(payload);
        if (payload.type === "trace") {
          return;
        }
        if (payload.type === "usage") {
          tokenCount.textContent = payload.tokens ?? tokenCount.textContent;
          return;
        }
        if (payload.type === "complete") {
          if (!assistantText && payload.reply) {
            assistantText = payload.reply;
            assistantView.content.textContent = assistantText;
          }
          turnCount.textContent = payload.turns ?? turnCount.textContent;
          tokenCount.textContent = payload.tokens ?? tokenCount.textContent;
          status.textContent = "Ready";
          loopStatus.textContent = "Complete";
          bindAssistantDetail(assistantView, payload);
          return;
        }
        if (payload.type === "error") {
          throw new Error(payload.message || "Request failed");
        }
      }

      try {
        const response = await fetch("/api/chat/stream", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({message: text}),
          signal: currentAbortController.signal
        });
        if (!response.ok) {
          const payload = await response.json();
          throw new Error(payload.error || "Request failed");
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const {value, done} = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream: true});
          const chunks = buffer.split("\\n\\n");
          buffer = chunks.pop();
          for (const chunk of chunks) processSseChunk(chunk, handlePayload);
        }
        buffer += decoder.decode();
        if (buffer.trim()) processSseChunk(buffer, handlePayload);
      } catch (error) {
        if (error.name === "AbortError") {
          const stopPayload = {
            type: "cancelled",
            label: "Stopped",
            message: "Run stopped by user"
          };
          assistantTrace.push(stopPayload);
          bindAssistantDetail(assistantView, stopPayload);
          status.textContent = "Stopped";
          loopStatus.textContent = "Stopped";
          return;
        }
        appendMessage("error", error.message);
        status.textContent = "Error";
        loopStatus.textContent = "Error";
      } finally {
        currentAbortController = null;
        sendButton.disabled = false;
        stopButton.disabled = true;
        input.focus();
      }
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      sendMessage(text);
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    resetButton.addEventListener("click", async () => {
      await fetch("/api/reset", {method: "POST"});
      messages.innerHTML = "";
      hideAssistantTrace();
      turnCount.textContent = "0";
      tokenCount.textContent = "0";
      status.textContent = "Ready";
      input.focus();
    });

    stopButton.addEventListener("click", async () => {
      stopButton.disabled = true;
      status.textContent = "Stopping";
      loopStatus.textContent = "Stopping";
      if (currentAbortController) currentAbortController.abort();
      await fetch("/api/cancel", {method: "POST"});
    });

    notesToggle.addEventListener("change", () => {
      if (activeAssistantView) renderAssistantTrace(activeAssistantView);
    });

    rawToggle.addEventListener("change", () => {
      if (selectedInspectorNode) renderInspectorDetail(selectedInspectorNode);
    });

    closeTraceButton.addEventListener("click", () => {
      hideAssistantTrace();
      input.focus();
    });

    input.focus();
  </script>
</body>
</html>"""


def _truncate(value: Any, limit: int = 1200) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def _parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _usage_payload(usage: Any) -> dict[str, int]:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


def _stream_event_payload(
    event: Any,
    *,
    run_id: str,
    turn_id: int,
    cost: float | None = None,
) -> dict[str, Any] | None:
    if event.type == "turn_start":
        return {
            "type": "trace",
            "stage": "turn_start",
            "label": f"Turn {event.turn} started",
            "run_id": run_id,
            "turn_id": event.turn or turn_id,
        }
    if event.type == "turn_end":
        return {
            "type": "trace",
            "stage": "turn_end",
            "label": f"Turn {event.turn} ended",
            "run_id": run_id,
            "turn_id": event.turn or turn_id,
        }
    if event.type == "thinking_delta":
        return {
            "type": "trace",
            "stage": "model_note",
            "label": "Model note",
            "detail": _truncate(event.thinking),
            "run_id": run_id,
            "turn_id": turn_id,
        }
    if event.type == "finish":
        return {
            "type": "trace",
            "stage": "llm_finish",
            "label": "LLM finished",
            "detail": event.finish_reason or "",
            "run_id": run_id,
            "turn_id": turn_id,
        }
    if event.type == "tool_call_start":
        return {
            "type": "trace",
            "stage": "tool_call",
            "label": f"Tool: {event.tool_call_name or 'unknown'}",
            "detail": _truncate(event.tool_call_arguments),
            "run_id": run_id,
            "turn_id": event.turn or turn_id,
            "tool_call_id": event.tool_call_id,
            "tool_name": event.tool_call_name,
            "arguments": _parse_jsonish(event.tool_call_arguments or ""),
        }
    if event.type == "tool_call_result":
        return {
            "type": "trace",
            "stage": "tool_result",
            "label": "Tool result" + (" failed" if event.tool_is_error else ""),
            "detail": _truncate(event.tool_result_content),
            "run_id": run_id,
            "turn_id": turn_id,
            "tool_call_id": event.tool_call_id,
            "result": _parse_jsonish(event.tool_result_content or ""),
            "is_error": event.tool_is_error,
        }
    if event.type == "tool_progress":
        progress = event.tool_progress_data or {}
        return {
            "type": "trace",
            "stage": "tool_progress",
            "label": event.tool_progress_message or event.tool_progress_type or "Tool progress",
            "detail": _truncate(json.dumps(progress, ensure_ascii=False)),
            "run_id": run_id,
            "turn_id": event.turn or turn_id,
            "tool_call_id": event.tool_call_id,
            "progress_type": event.tool_progress_type,
            "progress": progress,
        }
    if event.type == "usage" and event.usage:
        return {
            "type": "usage",
            "tokens": event.usage.total_tokens,
            "prompt_tokens": event.usage.prompt_tokens,
            "completion_tokens": event.usage.completion_tokens,
            "run_id": run_id,
            "turn_id": turn_id,
        }
    if event.type == "text_delta":
        return {
            "type": "assistant_delta",
            "text": event.text or "",
            "run_id": run_id,
            "turn_id": turn_id,
        }
    if event.type == "error":
        return {
            "type": "error",
            "message": event.error or "Unknown error",
            "code": event.error_code,
            "run_id": run_id,
            "turn_id": turn_id,
        }
    if event.type == "run_complete" and event.result:
        resolved_cost = round(cost or 0, 8)
        usage = _usage_payload(event.result.usage)
        return {
            "type": "complete",
            "run_id": run_id,
            "turn_id": turn_id,
            "reply": event.result.final_text,
            "turns": event.result.turn_count,
            "tokens": event.result.usage.total_tokens,
            "cost": resolved_cost,
            "cost_status": "estimated" if resolved_cost > 0 else "unavailable",
            "summary": {
                "run_id": run_id,
                "turn_id": turn_id,
                "reply": event.result.final_text,
                "turns": event.result.turn_count,
                "usage": usage,
                "cost": resolved_cost,
                "cost_status": "estimated" if resolved_cost > 0 else "unavailable",
            },
        }
    return None


@dataclass
class ChatbotWebApp:
    """Thread-safe facade used by the HTTP handler."""

    chatbot: Chatbot

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._run_counter = 0

    def _run_async(self, coro: Any) -> Any:
        return self._loop.run_until_complete(coro)

    def chat(self, message: str) -> dict[str, Any]:
        prompt = message.strip()
        if not prompt:
            raise ValueError("Message is required")

        with self._lock:
            result = self._run_async(self.chatbot.ask(prompt))
            return {
                "reply": result.final_text,
                "turns": result.turn_count,
                "tokens": result.usage.total_tokens,
                "cost": round(self.chatbot.agent.cost_tracker.get_cost(), 8),
            }

    def stream_chat(self, message: str, emit: Callable[[dict[str, Any]], None]) -> None:
        prompt = message.strip()
        if not prompt:
            raise ValueError("Message is required")

        async def run() -> None:
            current_turn = 0
            finish_reason = ""
            usage: dict[str, int] | None = None
            tool_calls: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            async for event in self.chatbot.stream(prompt):
                if event.turn:
                    current_turn = event.turn
                payload = _stream_event_payload(
                    event,
                    run_id=run_id,
                    turn_id=current_turn,
                    cost=self.chatbot.agent.cost_tracker.get_cost(),
                )
                if payload:
                    if payload.get("stage") == "llm_finish":
                        finish_reason = str(payload.get("detail", ""))
                    if payload.get("type") == "usage":
                        usage = {
                            "prompt_tokens": int(payload["prompt_tokens"]),
                            "completion_tokens": int(payload["completion_tokens"]),
                            "total_tokens": int(payload["tokens"]),
                        }
                    if payload.get("stage") == "tool_call":
                        tool_calls.append(payload)
                    if payload.get("stage") == "tool_result":
                        tool_results.append(payload)
                    if payload.get("type") == "complete":
                        payload["summary"].update(
                            {
                                "input": prompt,
                                "finish_reason": finish_reason,
                                "usage": usage or payload["summary"]["usage"],
                                "tool_calls": tool_calls,
                                "tool_results": tool_results,
                            }
                        )
                    emit(payload)

        with self._lock:
            self._run_counter += 1
            run_number = self._run_counter
            run_id = f"run-{run_number}"
            emit(
                {
                    "type": "run_start",
                    "run_id": run_id,
                    "turn_id": 0,
                    "input": prompt,
                    "label": f"Run {run_number}",
                }
            )
            self._run_async(run())

    def cancel(self) -> dict[str, bool]:
        self.chatbot.agent.abort()
        return {"ok": True}

    def reset(self) -> dict[str, bool]:
        with self._lock:
            self.chatbot.reset()
        return {"ok": True}


def _handler_for(app: ChatbotWebApp) -> type[BaseHTTPRequestHandler]:
    class ChatbotRequestHandler(BaseHTTPRequestHandler):
        server_version = "CalciferChatbot/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(min(length, 1_000_000))
            return json.loads(raw.decode("utf-8") or "{}")

        def _send_bytes(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self._send_bytes(status, body, "application/json; charset=utf-8")

        def _send_sse_headers(self) -> None:
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

        def _send_sse(self, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.wfile.write(b"data: " + data + b"\n\n")
            self.wfile.flush()

        def do_GET(self) -> None:
            if self.path == "/" or self.path == "/index.html":
                self._send_bytes(
                    HTTPStatus.OK,
                    render_index_html().encode("utf-8"),
                    "text/html; charset=utf-8",
                )
                return
            if self.path == "/healthz":
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:
            if self.path == "/api/reset":
                self._send_json(HTTPStatus.OK, app.reset())
                return
            if self.path == "/api/cancel":
                self._send_json(HTTPStatus.OK, app.cancel())
                return
            if self.path not in {"/api/chat", "/api/chat/stream"}:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return

            try:
                payload = self._read_json()
                message = str(payload.get("message", ""))
                if self.path == "/api/chat":
                    self._send_json(HTTPStatus.OK, app.chat(message))
                    return

                if not message.strip():
                    raise ValueError("Message is required")
                self._send_sse_headers()
                try:
                    app.stream_chat(message, self._send_sse)
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as exc:
                    try:
                        self._send_sse({"type": "error", "message": str(exc)})
                    except (BrokenPipeError, ConnectionResetError):
                        return
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    return ChatbotRequestHandler


def run_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    provider: ProviderMode = "deepseek",
    model: str | None = None,
    base_url: str | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    tools: ToolMode = "readonly",
    open_browser: bool = True,
) -> ThreadingHTTPServer:
    """Create and run the local chatbot web server."""
    chatbot = build_chatbot(
        provider=provider,
        model=model,
        base_url=base_url,
        system_prompt=system_prompt,
        tools=tools,
    )
    app = ChatbotWebApp(chatbot)
    server = ThreadingHTTPServer((host, port), _handler_for(app))
    url = f"http://{host}:{server.server_port}"
    print(f"Calcifer Chatbot web UI: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the Calcifer chatbot web UI.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--provider", choices=["deepseek", "openai"], default="deepseek")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--tools", choices=["none", "readonly", "all"], default="readonly")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser window.")
    args = parser.parse_args()

    run_server(
        host=args.host,
        port=args.port,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        system_prompt=args.system_prompt,
        tools=args.tools,
        open_browser=not args.no_open,
    )


if __name__ == "__main__":
    main()
