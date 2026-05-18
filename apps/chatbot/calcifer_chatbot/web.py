"""Local web UI for the Calcifer chatbot."""

from __future__ import annotations

import argparse
import asyncio
import html
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
SWITCHABLE_TOOL_MODES: tuple[ToolMode, ...] = ("chatbot", "workspace", "all")


def render_index_html(*, tool_mode: ToolMode = "chatbot") -> str:
    """Return the single-page chatbot UI."""
    document = """<!doctype html>
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
      grid-template-columns: minmax(0, 1fr) clamp(420px, 34vw, 560px);
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
    .mode-switcher {
      flex: 0 0 auto;
      display: inline-grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 2px;
      min-height: 28px;
      padding: 2px;
      border: 1px solid var(--line);
      background: var(--surface-soft);
      border-radius: 7px;
    }
    .mode-button {
      min-width: 72px;
      min-height: 22px;
      padding: 3px 8px;
      border: 0;
      background: transparent;
      color: var(--muted-strong);
      cursor: pointer;
      font-size: 12px;
      font-weight: 650;
    }
    .mode-button.is-active {
      background: var(--surface);
      color: var(--ink);
      box-shadow: 0 1px 0 rgba(31, 31, 29, 0.06);
    }
    .mode-button:disabled {
      cursor: wait;
      opacity: 0.62;
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
      grid-template-rows: auto auto minmax(0, 1fr) auto;
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
    .trace-tabs {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 6px;
      padding: 9px 12px;
      border-bottom: 1px solid var(--line);
      background: rgba(247, 247, 244, 0.74);
    }
    .trace-tab {
      min-width: 0;
      min-height: 30px;
      padding: 5px 8px;
      border: 1px solid transparent;
      background: transparent;
      color: var(--muted-strong);
      cursor: pointer;
      font-size: 12px;
      font-weight: 650;
    }
    .trace-tab.is-active {
      border-color: var(--line-strong);
      background: var(--surface);
      color: var(--ink);
      box-shadow: 0 1px 0 rgba(31, 31, 29, 0.04);
    }
    .agent-loop-events {
      min-height: 0;
      overflow-y: auto;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .run-summary-card,
    .trace-step,
    .tool-group-card,
    .source-card,
    .empty-state {
      min-width: 0;
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--line);
      background: var(--surface);
      border-radius: 8px;
    }
    .run-summary-card {
      border-color: #b9d8d3;
      background: var(--accent-soft);
    }
    .run-summary-title,
    .trace-step-title,
    .tool-group-title,
    .source-card-title {
      min-width: 0;
      color: var(--ink);
      font-size: 12px;
      line-height: 1.3;
      font-weight: 750;
    }
    .run-summary-meta,
    .trace-step-meta,
    .tool-group-meta,
    .source-card-meta,
    .empty-state {
      color: var(--muted);
      font-size: 11px;
      line-height: 1.45;
    }
    .run-stat-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .run-stat {
      min-width: 0;
      padding: 8px;
      border: 1px solid rgba(15, 118, 110, 0.18);
      background: rgba(255, 255, 255, 0.72);
      border-radius: 7px;
    }
    .run-stat-value {
      color: var(--ink);
      font-size: 16px;
      line-height: 1.2;
      font-weight: 750;
    }
    .run-stat-label {
      margin-top: 2px;
      color: var(--muted);
      font-size: 10px;
      line-height: 1.3;
    }
    .trace-section-title {
      margin-top: 4px;
      color: var(--muted-strong);
      font-size: 11px;
      line-height: 1.3;
      font-weight: 750;
      text-transform: uppercase;
    }
    .run-path {
      margin: 0;
      padding-left: 18px;
      display: grid;
      gap: 6px;
      color: var(--muted-strong);
      font-size: 12px;
      line-height: 1.45;
    }
    .loop-card {
      min-width: 0;
      display: grid;
      gap: 9px;
      padding: 10px;
      border: 1px solid var(--line);
      background: var(--surface);
      border-radius: 8px;
    }
    .loop-card-head {
      min-width: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .loop-card-title {
      min-width: 0;
      color: var(--ink);
      font-size: 12px;
      font-weight: 750;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .loop-card-meta {
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 11px;
    }
    .loop-card-body {
      min-width: 0;
      display: grid;
      gap: 7px;
      padding-left: 12px;
      border-left: 1px solid var(--line-strong);
    }
    .loop-stage {
      min-width: 0;
      display: grid;
      gap: 6px;
      padding: 8px 9px;
      border: 1px solid var(--line);
      background: rgba(247, 247, 244, 0.72);
      border-radius: 8px;
      cursor: pointer;
    }
    .loop-stage[data-kind="input"],
    .loop-stage[data-kind="response"] {
      background: var(--surface);
    }
    .loop-stage[data-kind="observation"][data-status="failed"] {
      border-color: #efb2ad;
      background: #fff6f5;
    }
    .loop-stage-head {
      min-width: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .loop-stage-title {
      min-width: 0;
      color: var(--ink);
      font-size: 12px;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .loop-stage-meta {
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 11px;
    }
    .loop-stage-preview {
      color: var(--muted-strong);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 11px;
      line-height: 1.45;
    }
    .loop-stage-detail {
      min-width: 0;
      display: grid;
      gap: 7px;
      padding-top: 2px;
    }
    .loop-stage-detail[hidden] {
      display: none;
    }
    .reasoning-timeline {
      min-width: 0;
      display: grid;
      gap: 8px;
      position: relative;
      padding-left: 12px;
      border-left: 1px solid var(--line-strong);
    }
    .timeline-step {
      min-width: 0;
      display: grid;
      gap: 6px;
      padding: 8px 9px;
      border: 1px solid var(--line);
      background: rgba(247, 247, 244, 0.72);
      border-radius: 8px;
      cursor: pointer;
    }
    .timeline-step[data-kind="input"],
    .timeline-step[data-kind="response"] {
      background: var(--surface);
    }
    .timeline-step[data-kind="thought"] {
      background: #fbfbf8;
    }
    .timeline-step[data-kind="action"],
    .timeline-step[data-kind="action"] + .timeline-step[data-kind="observation"] {
      border-left: 3px solid var(--accent);
    }
    .timeline-step[data-kind="action"] {
      border-bottom-left-radius: 4px;
      border-bottom-right-radius: 4px;
      margin-bottom: -3px;
    }
    .timeline-step[data-kind="action"] + .timeline-step[data-kind="observation"] {
      border-top-left-radius: 4px;
      border-top-right-radius: 4px;
    }
    .timeline-step[data-kind="observation"][data-status="failed"] {
      border-color: #efb2ad;
      background: #fff6f5;
    }
    .timeline-step-head {
      min-width: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .timeline-step-title {
      min-width: 0;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--ink);
      font-size: 12px;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .timeline-title-label {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .timeline-turn-badge {
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 18px;
      height: 18px;
      padding: 0 6px;
      border: 1px solid #e4c36f;
      background: #fff6d9;
      color: #7c4d00;
      border-radius: 999px;
      font-size: 10px;
      line-height: 1;
      font-weight: 800;
      font-variant-numeric: tabular-nums;
    }
    .timeline-head-trailing {
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
    }
    .timeline-step-meta {
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 11px;
    }
    .timeline-info-button {
      flex: 0 0 auto;
      width: 18px;
      height: 18px;
      border: 1px solid var(--line-strong);
      background: var(--surface);
      color: var(--muted-strong);
      border-radius: 999px;
      font-size: 11px;
      line-height: 1;
      font-weight: 750;
      cursor: pointer;
    }
    .timeline-info-button:hover {
      border-color: var(--accent);
      color: var(--accent);
      background: var(--accent-soft);
    }
    .timeline-step-preview {
      color: var(--muted-strong);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 11px;
      line-height: 1.45;
    }
    .timeline-step-detail {
      min-width: 0;
      display: grid;
      gap: 7px;
      padding-top: 2px;
    }
    .timeline-step-detail[hidden] {
      display: none;
    }
    .timeline-children {
      min-width: 0;
      display: grid;
      gap: 7px;
      padding-left: 10px;
      border-left: 1px solid var(--line);
    }
    .trace-step {
      cursor: pointer;
    }
    .trace-step.is-selected,
    .tool-group-card.is-selected,
    .source-card.is-selected {
      border-color: var(--accent);
      box-shadow: 0 0 0 1px rgba(15, 118, 110, 0.12);
    }
    .trace-step-head,
    .tool-group-head,
    .source-card-head {
      min-width: 0;
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 8px;
    }
    .trace-step-preview,
    .tool-group-preview,
    .source-card-snippet {
      color: var(--muted-strong);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 11px;
      line-height: 1.45;
    }
    .structured-json,
    .tool-group-json {
      min-width: 0;
      display: grid;
      gap: 6px;
      color: var(--muted-strong);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 11px;
      line-height: 1.45;
    }
    .structured-json-object,
    .structured-json-array {
      min-width: 0;
      display: grid;
      gap: 5px;
    }
    .json-field-row {
      min-width: 0;
      display: grid;
      grid-template-columns: minmax(120px, 38%) minmax(0, 1fr);
      gap: 8px;
      padding: 6px 7px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.66);
      border-radius: 7px;
    }
    .json-field-key {
      min-width: 0;
      color: var(--muted);
      font-weight: 700;
      overflow-wrap: break-word;
    }
    .json-field-value {
      min-width: 0;
      color: var(--muted-strong);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .json-field-value.is-nested {
      display: grid;
      gap: 5px;
    }
    .json-toggle {
      min-width: 0;
      display: grid;
      gap: 5px;
    }
    .json-toggle-summary {
      min-width: 0;
      display: flex;
      align-items: baseline;
      gap: 7px;
      color: var(--muted-strong);
      cursor: pointer;
      list-style-position: outside;
    }
    .json-toggle-summary::-webkit-details-marker {
      color: var(--muted);
    }
    .json-summary-key {
      color: var(--ink);
      font-weight: 750;
      overflow-wrap: anywhere;
    }
    .json-summary-meta {
      color: var(--muted);
      font-size: 10px;
      font-weight: 650;
    }
    .json-toggle-body {
      min-width: 0;
      display: grid;
      gap: 5px;
      padding-left: 14px;
      border-left: 1px solid var(--line);
    }
    .json-primitive[data-type="number"],
    .json-primitive[data-type="boolean"] {
      color: #0f625d;
    }
    .json-primitive[data-type="null"] {
      color: var(--muted);
      font-style: italic;
    }
    .tool-group-json {
      gap: 4px;
    }
    .tool-group-json .json-field-row {
      grid-template-columns: minmax(120px, 38%) minmax(0, 1fr);
      padding: 5px 7px;
      background: rgba(255, 255, 255, 0.54);
    }
    .tool-group-json .json-field-key {
      overflow-wrap: normal;
      white-space: nowrap;
    }
    .timeline-step-detail > .tool-group-json .json-field-row {
      grid-template-columns: minmax(0, 1fr);
      gap: 3px;
    }
    .timeline-step-detail > .tool-group-json .json-field-key {
      color: var(--muted);
      white-space: normal;
    }
    .tool-group-card {
      margin-left: 12px;
      background: rgba(247, 247, 244, 0.72);
    }
    .tool-group-card[data-kind="tool_result_error"] {
      border-color: #efb2ad;
      background: #fff6f5;
    }
    .source-card {
      cursor: pointer;
    }
    .source-card a {
      color: var(--accent);
      text-decoration: none;
    }
    .source-card a:hover {
      text-decoration: underline;
    }
    .raw-payload {
      margin: 0;
      min-height: 0;
      overflow: auto;
      padding: 10px 12px;
      color: var(--muted-strong);
      background: var(--surface-code);
      border: 1px solid var(--line);
      border-radius: 8px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 11px;
      line-height: 1.5;
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
    .loop-detail-head {
      min-width: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      min-height: 38px;
      padding: 7px 12px;
      border-bottom: 1px solid var(--line);
    }
    .loop-detail-title {
      min-width: 0;
      color: var(--ink);
      font-size: 12px;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .loop-detail-actions {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .loop-detail-filter {
      width: 150px;
      min-height: 26px;
      padding: 4px 7px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--ink);
      border-radius: 7px;
      font-size: 12px;
    }
    .copy-payload-button {
      min-height: 26px;
      padding: 4px 8px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--muted-strong);
      cursor: pointer;
      font-size: 12px;
    }
    .loop-detail-payload {
      margin: 0;
      min-height: 0;
      overflow: auto;
      padding: 10px 12px;
      color: var(--muted-strong);
      background: var(--surface-code);
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 11px;
      line-height: 1.5;
    }
    .loop-detail-payload .json-field-row {
      grid-template-columns: minmax(0, 1fr);
      gap: 4px;
    }
    .loop-detail-payload .json-field-key {
      overflow-wrap: anywhere;
      white-space: normal;
    }
    .loop-detail-payload .json-field-value {
      padding-left: 8px;
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
    .message[data-role="assistant"].is-pending .message-content {
      color: var(--muted);
      font-style: italic;
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
    .message-extras {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      padding-top: 4px;
    }
    .trace-capsule {
      min-height: 28px;
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 5px 9px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.74);
      color: var(--muted-strong);
      border-radius: 999px;
      cursor: pointer;
      font-size: 12px;
      line-height: 1.2;
    }
    .trace-capsule:hover {
      border-color: var(--line-strong);
      color: var(--ink);
    }
    .trace-capsule-dot {
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--accent);
    }
    .activity-path {
      width: 100%;
      margin: 0;
      padding: 2px 0 0 18px;
      display: grid;
      gap: 4px;
      color: var(--muted-strong);
      font-size: 12px;
      line-height: 1.42;
    }
    .activity-path-item {
      min-width: 0;
      padding-left: 2px;
      overflow-wrap: anywhere;
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
      grid-template-columns: minmax(0, 1fr) clamp(420px, 34vw, 560px);
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
      .codex-workspace-grid.has-trace,
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
          <div id="mode-switcher" class="mode-switcher" role="group" aria-label="Tool mode">
            <button class="mode-button" type="button" data-mode-option="chatbot">chatbot</button>
            <button class="mode-button" type="button" data-mode-option="workspace">workspace</button>
            <button class="mode-button" type="button" data-mode-option="all">all</button>
          </div>
          <span id="tool-mode-pill" class="status-pill">__TOOL_MODE__</span>
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
            <div class="agent-loop-title">Run details</div>
            <div class="agent-loop-status">
              <span id="loop-status">Idle</span>
              <button id="close-trace-button" class="ghost-button" type="button">Close</button>
            </div>
          </div>
          <div class="trace-tabs" role="tablist" aria-label="Run detail views">
            <button id="overview-tab" class="trace-tab is-active" type="button" data-trace-tab="overview">Overview</button>
            <button id="steps-tab" class="trace-tab" type="button" data-trace-tab="steps">Steps</button>
            <button id="sources-tab" class="trace-tab" type="button" data-trace-tab="sources">Sources</button>
            <button id="raw-tab" class="trace-tab" type="button" data-trace-tab="raw">Raw</button>
          </div>
          <section id="agent-loop-events" class="agent-loop-events" aria-live="polite"></section>
          <section id="agent-loop-detail" class="loop-detail" hidden>
            <div class="loop-detail-head">
              <div id="agent-loop-detail-title" class="loop-detail-title">Payload</div>
              <div class="loop-detail-actions">
                <input id="loop-detail-filter" class="loop-detail-filter" type="search" placeholder="Filter fields" autocomplete="off">
                <button id="copy-payload-button" class="copy-payload-button" type="button">Copy</button>
              </div>
            </div>
            <div id="agent-loop-payload" class="loop-detail-payload">{}</div>
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
    const loopDetailFilter = document.getElementById("loop-detail-filter");
    const copyPayloadButton = document.getElementById("copy-payload-button");
    const loopStatus = document.getElementById("loop-status");
    const status = document.getElementById("status");
    const sendButton = document.getElementById("send-button");
    const resetButton = document.getElementById("reset-button");
    const closeTraceButton = document.getElementById("close-trace-button");
    const stopButton = document.getElementById("stop-button");
    const toolModePill = document.getElementById("tool-mode-pill");
    const modeButtons = Array.from(document.querySelectorAll("[data-mode-option]"));
    const turnCount = document.getElementById("turn-count");
    const tokenCount = document.getElementById("token-count");
    const traceTabs = Array.from(document.querySelectorAll("[data-trace-tab]"));
    let currentAbortController = null;
    let activeAssistantView = null;
    let selectedInspectorNode = null;
    let selectedInspectorPayload = null;
    let activeTraceTab = "overview";
    let activeMode = "__TOOL_MODE__";

    function scrollToBottom() {
      threadWrap.scrollTop = threadWrap.scrollHeight;
    }

    function timeLabel() {
      return new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    }

    function setActiveMode(mode) {
      activeMode = mode;
      if (toolModePill) toolModePill.textContent = mode;
      modeButtons.forEach((button) => {
        const isActive = button.dataset.modeOption === mode;
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-pressed", isActive ? "true" : "false");
      });
    }

    function clearConversationView() {
      messages.innerHTML = "";
      hideAssistantTrace();
      turnCount.textContent = "0";
      tokenCount.textContent = "0";
      status.textContent = "Ready";
      loopStatus.textContent = "Idle";
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

      const extras = document.createElement("div");
      extras.className = "message-extras";

      node.append(meta);
      node.append(content);
      node.append(extras);
      messages.appendChild(node);
      scrollToBottom();
      return {node, content, extras};
    }

    function setAssistantPlaceholder(assistantView, text) {
      if (!assistantView || assistantView.textStarted) return;
      assistantView.node.classList.add("is-pending");
      assistantView.content.textContent = text;
      scrollToBottom();
    }

    function clearAssistantPlaceholder(assistantView) {
      if (!assistantView) return;
      assistantView.node.classList.remove("is-pending");
    }

    function ensureAssistantPath(assistantView) {
      if (!assistantView.activityPath || !assistantView.activityPath.isConnected) {
        const path = document.createElement("ol");
        path.className = "activity-path";
        assistantView.activityPath = path;
        assistantView.extras.prepend(path);
      }
      return assistantView.activityPath;
    }

    function appendAssistantPathItem(assistantView, item) {
      if (!assistantView || !item || !item.label) return;
      if (!assistantView.pathItems) assistantView.pathItems = new Map();
      const key = item.key || item.label;
      let node = assistantView.pathItems.get(key);
      if (!node) {
        node = document.createElement("li");
        node.className = "activity-path-item";
        assistantView.pathItems.set(key, node);
        ensureAssistantPath(assistantView).appendChild(node);
      }
      node.textContent = item.label;
    }

    function toolQueryFromPayload(payload) {
      const args = parseMaybeJson(payload.arguments || payload.detail || {});
      if (args && typeof args === "object" && args.query) return args.query;
      if (payload.progress && payload.progress.query) return payload.progress.query;
      return "";
    }

    function updateAssistantPathFromTrace(assistantView, payload) {
      if (!payload || payload.type !== "trace") return;
      if (payload.stage === "model_note") {
        appendAssistantPathItem(assistantView, {key: "thinking", label: "Thinking through request"});
        return;
      }
      if (payload.stage === "tool_call") {
        const query = toolQueryFromPayload(payload);
        if (payload.tool_name === "web_search" && query) {
          appendAssistantPathItem(assistantView, {key: `search-${payload.tool_call_id || query}`, label: `Searching web: ${query}`});
        } else {
          appendAssistantPathItem(assistantView, {key: `tool-${payload.tool_call_id || payload.tool_name || "unknown"}`, label: `Using ${payload.tool_name || "tool"}`});
        }
        return;
      }
      if (payload.stage === "tool_progress" && payload.progress_type === "query_update") {
        const query = toolQueryFromPayload(payload);
        if (query) appendAssistantPathItem(assistantView, {key: `search-${payload.tool_call_id || query}`, label: `Searching web: ${query}`});
        return;
      }
      if (payload.stage === "tool_progress" && payload.progress_type === "search_results_received") {
        const query = toolQueryFromPayload(payload);
        const resultCount = payload.progress?.result_count ?? 0;
        appendAssistantPathItem(assistantView, {
          key: `results-${payload.tool_call_id || query}`,
          label: query ? `Found ${resultCount} results for ${query}` : `Found ${resultCount} results`,
        });
        return;
      }
      if (payload.stage === "tool_result") {
        appendAssistantPathItem(assistantView, {key: `result-${payload.tool_call_id || "tool"}`, label: payload.is_error ? "Tool result failed" : "Reading tool result"});
        return;
      }
      if (payload.stage === "llm_finish") {
        appendAssistantPathItem(assistantView, {key: `finish-${payload.turn_id || "model"}`, label: payload.detail ? `Model finished: ${payload.detail}` : "Model finished"});
      }
    }

    function formatStructuredValue(value) {
      if (value === null || value === undefined || value === "") return "";
      if (typeof value === "object") return JSON.stringify(value, null, 2);
      return String(value);
    }

    function isStructuredObject(value) {
      return value && typeof value === "object";
    }

    function primitiveType(value) {
      if (value === null) return "null";
      if (Array.isArray(value)) return "array";
      return typeof value;
    }

    function renderPrimitiveValue(value) {
      const node = document.createElement("span");
      node.className = "json-primitive";
      node.dataset.type = primitiveType(value);
      if (value === null) node.textContent = "null";
      else if (value === undefined) node.textContent = "undefined";
      else if (typeof value === "string") node.textContent = value;
      else node.textContent = String(value);
      return node;
    }

    function renderStructuredValue(value, options = {}) {
      const parsed = parseMaybeJson(value);
      const root = document.createElement("div");
      root.className = "structured-json";
      appendStructuredNode(root, parsed, {
        depth: options.depth || 0,
        maxDepth: options.maxDepth || 5,
        maxItems: options.maxItems || 60,
      });
      return root;
    }

    function structuredNodeSummary(value) {
      const parsed = parseMaybeJson(value);
      if (Array.isArray(parsed)) return `[${parsed.length}]`;
      if (parsed && typeof parsed === "object") {
        const keys = Object.keys(parsed).length;
        return `{${keys} ${keys === 1 ? "field" : "fields"}}`;
      }
      return primitiveType(parsed);
    }

    function renderStructuredSummary(key, value) {
      const fragment = document.createDocumentFragment();
      const keyNode = document.createElement("span");
      keyNode.className = "json-summary-key";
      keyNode.textContent = key;
      const metaNode = document.createElement("span");
      metaNode.className = "json-summary-meta";
      metaNode.textContent = structuredNodeSummary(value);
      fragment.append(keyNode, metaNode);
      return fragment;
    }

    function appendStructuredNode(container, value, options) {
      const parsed = parseMaybeJson(value);
      const depth = options.depth || 0;
      const maxDepth = options.maxDepth || 5;
      const maxItems = options.maxItems || 60;

      if (!isStructuredObject(parsed)) {
        container.appendChild(renderPrimitiveValue(parsed));
        return;
      }
      if (depth >= maxDepth) {
        container.appendChild(renderPrimitiveValue(previewValue(parsed, 180)));
        return;
      }

      const entries = Array.isArray(parsed)
        ? parsed.map((item, index) => [String(index), item])
        : Object.entries(parsed);
      const list = document.createElement("div");
      list.className = Array.isArray(parsed) ? "structured-json-array" : "structured-json-object";
      entries.slice(0, maxItems).forEach(([key, item]) => {
        const row = document.createElement("div");
        row.className = "json-field-row";
        if (isStructuredObject(parseMaybeJson(item))) {
          const toggle = document.createElement("details");
          toggle.className = "json-toggle";
          toggle.open = depth < 2;
          const summary = document.createElement("summary");
          summary.className = "json-toggle-summary";
          summary.append(renderStructuredSummary(key, item));
          const body = document.createElement("div");
          body.className = "json-toggle-body";
          appendStructuredNode(body, item, {...options, depth: depth + 1});
          toggle.append(summary, body);
          row.appendChild(toggle);
        } else {
          const keyNode = document.createElement("div");
          keyNode.className = "json-field-key";
          keyNode.textContent = key;
          const valueNode = document.createElement("div");
          valueNode.className = "json-field-value";
          valueNode.appendChild(renderPrimitiveValue(item));
          row.append(keyNode, valueNode);
        }
        list.appendChild(row);
      });
      if (entries.length > maxItems) {
        const row = document.createElement("div");
        row.className = "json-field-row";
        const keyNode = document.createElement("div");
        keyNode.className = "json-field-key";
        keyNode.textContent = "more";
        const valueNode = document.createElement("div");
        valueNode.className = "json-field-value";
        valueNode.textContent = `${entries.length - maxItems} more item${entries.length - maxItems === 1 ? "" : "s"}`;
        row.append(keyNode, valueNode);
        list.appendChild(row);
      }
      container.appendChild(list);
    }

    function compactJsonRows(value) {
      const parsed = parseMaybeJson(value);
      if (!parsed || typeof parsed !== "object") return {};
      if (Array.isArray(parsed)) return {items: `${parsed.length} item${parsed.length === 1 ? "" : "s"}`};
      return Object.fromEntries(
        Object.entries(parsed)
          .filter(([, item]) => item !== undefined && item !== null && item !== "")
          .slice(0, 6)
          .map(([key, item]) => [key, isStructuredObject(parseMaybeJson(item)) ? previewValue(item, 120) : item])
      );
    }

    function appendJsonFieldRows(container, value) {
      const rows = compactJsonRows(value);
      const entries = Object.entries(rows);
      if (!entries.length) return null;
      const list = document.createElement("div");
      list.className = "tool-group-json";
      for (const [key, item] of entries) {
        const row = document.createElement("div");
        row.className = "json-field-row";
        const keyNode = document.createElement("div");
        keyNode.className = "json-field-key";
        keyNode.textContent = key;
        const valueNode = document.createElement("div");
        valueNode.className = "json-field-value";
        valueNode.textContent = previewValue(item, 180);
        row.append(keyNode, valueNode);
        list.appendChild(row);
      }
      container.appendChild(list);
      return list;
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

    function parseMaybeJson(value) {
      if (typeof value !== "string") return value;
      try {
        return JSON.parse(value);
      } catch {
        return value;
      }
    }

    function normalizedResultPayload(value) {
      const parsed = parseMaybeJson(value);
      return parsed && typeof parsed === "object" ? parsed : {};
    }

    function resultCountFromPayload(value) {
      const payload = normalizedResultPayload(value);
      if (typeof payload.result_count === "number") return payload.result_count;
      if (Array.isArray(payload.results)) return payload.results.length;
      return 0;
    }

    function hostLabel(url) {
      try {
        return new URL(url).hostname.replace(/^www\\./, "");
      } catch {
        return "";
      }
    }

    function sourceFromResult(item, query, callId) {
      return {
        id: `${callId || "source"}-${String(item.url || item.title || "").slice(0, 48)}`,
        title: item.title || item.url || "Untitled source",
        url: item.url || "",
        host: hostLabel(item.url || ""),
        snippet: item.snippet || "",
        query,
        tool_call_id: callId || "",
        detail: item,
      };
    }

    function extractSourcesFromResult(value, query, callId) {
      const payload = normalizedResultPayload(value);
      if (!Array.isArray(payload.results)) return [];
      return payload.results.map((item) => sourceFromResult(item, payload.query || query || "", callId));
    }

    function filterInspectorPayload(value, query) {
      const needle = String(query || "").trim().toLowerCase();
      if (!needle) return value;
      const parsed = parseMaybeJson(value);
      if (!isStructuredObject(parsed)) {
        return String(parsed).toLowerCase().includes(needle) ? parsed : "";
      }
      if (Array.isArray(parsed)) {
        return parsed
          .map((item) => filterInspectorPayload(item, needle))
          .filter((item) => item !== "" && item !== undefined && item !== null && !(Array.isArray(item) && item.length === 0));
      }
      const filtered = {};
      for (const [key, item] of Object.entries(parsed)) {
        const keyMatches = key.toLowerCase().includes(needle);
        const itemText = isStructuredObject(parseMaybeJson(item)) ? "" : String(item).toLowerCase();
        const child = filterInspectorPayload(item, needle);
        const hasChild = child !== "" && child !== undefined && child !== null && !(Array.isArray(child) && child.length === 0)
          && !(typeof child === "object" && !Array.isArray(child) && Object.keys(child).length === 0);
        if (keyMatches || itemText.includes(needle) || hasChild) filtered[key] = keyMatches ? item : child;
      }
      return filtered;
    }

    function renderInspectorPayload(payload, filterText = "") {
      const filtered = filterInspectorPayload(payload, filterText);
      loopDetailPayload.replaceChildren(renderStructuredValue(filtered));
    }

    function renderInspectorDetail(node) {
      if (!node) return;
      selectedInspectorNode = node;
      agentLoopDetail.hidden = false;
      agentLoopDetail.className = "loop-detail";
      loopDetailTitle.textContent = node.title || "Payload";
      const payload = node.raw || node.payload || node.detail || node;
      selectedInspectorPayload = payload;
      if (loopDetailFilter) loopDetailFilter.value = "";
      if (copyPayloadButton) copyPayloadButton.textContent = "Copy";
      renderInspectorPayload(payload);
    }

    async function copyInspectorPayload() {
      if (!selectedInspectorPayload) return;
      const text = JSON.stringify(selectedInspectorPayload, null, 2);
      try {
        await navigator.clipboard.writeText(text);
        copyPayloadButton.textContent = "Copied";
      } catch {
        copyPayloadButton.textContent = "Copy failed";
      }
    }

    function updateTraceTabs() {
      traceTabs.forEach((tab) => {
        tab.classList.toggle("is-active", tab.dataset.traceTab === activeTraceTab);
      });
    }

    function hideAssistantTrace() {
      workspaceGrid.classList.remove("has-trace");
      agentLoopPanel.hidden = true;
      agentLoopEvents.innerHTML = "";
      agentLoopDetail.hidden = true;
      loopDetailTitle.textContent = "Payload";
      loopDetailPayload.textContent = "{}";
      loopStatus.textContent = "Idle";
      activeTraceTab = "overview";
      updateTraceTabs();
      activeAssistantView = null;
      selectedInspectorNode = null;
      selectedInspectorPayload = null;
      if (loopDetailFilter) loopDetailFilter.value = "";
      if (copyPayloadButton) copyPayloadButton.textContent = "Copy";
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

    function buildTraceCapsule(summary) {
      const toolCalls = summary.tool_calls || [];
      const toolResults = summary.tool_results || [];
      const webSearches = toolCalls.filter((call) => call.tool_name === "web_search").length;
      const resultCount = toolResults.reduce((total, result) => total + resultCountFromPayload(result.result), 0);
      const toolLabel = webSearches
        ? `Searched web · ${webSearches} ${webSearches === 1 ? "query" : "queries"}`
        : toolCalls.length
          ? `${toolCalls.length} ${toolCalls.length === 1 ? "tool call" : "tool calls"}`
          : "Agent trace";
      return compactMeta([
        toolLabel,
        resultCount ? `${resultCount} results` : "",
        `${summary.turns || 0} ${summary.turns === 1 ? "model turn" : "model turns"}`,
        `${summary.usage.total_tokens || 0} tokens`,
      ]);
    }

    function groupToolEventsByCall(children) {
      const groups = [];
      const byId = new Map();
      const ensureGroup = (toolCallId) => {
        const id = toolCallId || `tool-${groups.length}`;
        if (!byId.has(id)) {
          const group = {
            id: `tool-group-${id}`,
            kind: "tool_group",
            title: "Tool",
            meta: shortId(id),
            tool_call_id: toolCallId || "",
            tool_name: "",
            call: null,
            progress: [],
            result: null,
            resultPayload: {},
            resultCount: 0,
            sources: [],
            detail: {},
            raw: {},
          };
          byId.set(id, group);
          groups.push(group);
        }
        return byId.get(id);
      };

      for (const child of children) {
        const toolCallId = child.detail?.tool_call_id || child.raw?.tool_call_id || "";
        const group = ensureGroup(toolCallId);
        if (child.kind === "tool_call") {
          group.call = child;
          group.tool_name = child.detail.tool_name || "";
        } else if (child.kind === "tool_progress") {
          group.progress.push(child);
        } else if (child.kind === "tool_result" || child.kind === "tool_result_error") {
          group.result = child;
          group.kind = child.kind === "tool_result_error" ? "tool_result_error" : "tool_group";
          group.resultPayload = normalizedResultPayload(child.detail.result);
          group.resultCount = resultCountFromPayload(child.detail.result);
        }
      }

      for (const group of groups) {
        const args = group.call?.detail?.arguments || {};
        const query = args.query || group.resultPayload.query || group.progress.find((item) => item.detail?.progress?.query)?.detail.progress.query || "";
        group.tool_name = group.tool_name || group.call?.detail?.tool_name || "unknown";
        group.title = group.tool_name === "web_search" ? "Search web" : `Run ${group.tool_name}`;
        group.meta = compactMeta([
          group.result?.detail?.is_error ? "failed" : group.result ? "success" : "running",
          group.resultCount ? `${group.resultCount} results` : "",
        ]);
        group.preview = query
          ? `Query: ${query}`
          : previewValue(args || group.resultPayload || group.progress.map((item) => item.detail), 180);
        group.sources = extractSourcesFromResult(group.result?.detail?.result, query, group.tool_call_id);
        group.detail = {
          tool_name: group.tool_name,
          tool_call_id: group.tool_call_id,
          arguments: args,
          progress: group.progress.map((item) => item.detail),
          result: group.resultPayload,
        };
        group.raw = {
          call: group.call?.raw || null,
          progress: group.progress.map((item) => item.raw),
          result: group.result?.raw || null,
        };
      }
      return groups;
    }

    function buildRunDetailsModel(assistantView) {
      const summary = buildRunSummary(assistantView);
      const trace = (assistantView.trace || []).filter((payload) => payload && payload.type !== "assistant_delta");
      const turns = new Map();
      const turnNodes = [];
      const tools = new Map();
      let finalNode = null;
      let terminalNode = null;

      function ensureTurn(turnId) {
        const resolved = Number(turnId || 1);
        if (!turns.has(resolved)) {
          const node = {
            id: `turn-${resolved}`,
            kind: "model",
            title: `Model turn ${resolved}`,
            meta: `turn ${resolved}`,
            preview: "Waiting for model response",
            turn_id: resolved,
            finish_reason: "",
            usage: null,
            notes: [],
            children: [],
            llmInput: null,
            llmOutput: null,
            raw_events: [],
            detail: {turn_id: resolved, finish_reason: "", usage: null, notes: []},
          };
          turns.set(resolved, node);
          turnNodes.push(node);
        }
        return turns.get(resolved);
      }

      const inputPayload = trace.find((payload) => payload.type === "input");
      const inputNode = {
        id: "input",
        kind: "input",
        title: "User input",
        meta: "input",
        preview: previewValue(inputPayload?.detail || summary.input),
        detail: {run_id: summary.run_id, input: inputPayload?.detail || summary.input},
        raw: inputPayload || {type: "input", detail: summary.input},
      };

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
            title: "Response",
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
        if (payload.stage === "llm_input") {
          turn.llmInput = payload;
          continue;
        }
        if (payload.stage === "llm_output") {
          turn.llmOutput = payload;
          continue;
        }
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
        }
      }

      for (const turn of turns.values()) {
        const usage = turn.usage;
        turn.toolGroups = groupToolEventsByCall(turn.children);
        turn.meta = compactMeta([
          `turn ${turn.turn_id}`,
          turn.finish_reason ? `finish ${turn.finish_reason}` : "",
          usage ? `${usage.total_tokens} tokens` : "",
          turn.toolGroups.length ? `${turn.toolGroups.length} tool ${turn.toolGroups.length === 1 ? "call" : "calls"}` : "",
        ]);
        turn.preview = compactMeta([
          turn.notes.length ? `${turn.notes.length} model notes` : "",
          turn.toolGroups.length ? `${turn.toolGroups.length} grouped tool events` : "model response",
        ]);
        turn.detail = {
          turn_id: turn.turn_id,
          finish_reason: turn.finish_reason,
          usage,
          notes: turn.notes,
          llm_input: turn.llmInput?.detail || null,
          llm_output: turn.llmOutput?.detail || null,
          tools: turn.toolGroups.map((group) => group.detail),
        };
      }

      const toolGroups = turnNodes.flatMap((turn) => turn.toolGroups || []);
      const seenSources = new Set();
      const sources = [];
      for (const source of toolGroups.flatMap((group) => group.sources || [])) {
        const key = `${source.url}|${source.title}`;
        if (!key || seenSources.has(key)) continue;
        seenSources.add(key);
        sources.push(source);
      }
      const webSearchCount = toolGroups.filter((group) => group.tool_name === "web_search").length;
      const resultCount = toolGroups.reduce((total, group) => total + (group.resultCount || 0), 0);
      const statusLabel = terminalNode ? terminalNode.title : finalNode ? "Complete" : "Trace";
      const pathItems = [
        inputNode.preview ? "Input" : "",
        ...toolGroups
          .filter((group) => group.tool_name === "web_search")
          .map((group) => group.preview.replace(/^Query: /, "Search web for ")),
        resultCount ? `Observed ${resultCount} web result${resultCount === 1 ? "" : "s"}` : "",
        finalNode ? "Response" : "",
        terminalNode ? terminalNode.title : "",
      ].filter(Boolean);
      return {
        summary,
        trace,
        inputNode,
        turns: turnNodes,
        toolGroups,
        sources,
        finalNode,
        terminalNode,
        statusLabel,
        pathItems,
        stats: {
          modelTurns: turnNodes.length,
          toolCalls: toolGroups.length,
          webSearches: webSearchCount,
          resultCount,
          tokens: summary.usage.total_tokens || 0,
        },
      };
    }

    function selectTraceNode(node, element) {
      agentLoopEvents.querySelectorAll(".trace-step, .tool-group-card, .source-card").forEach((event) => {
        event.classList.remove("is-selected");
      });
      if (element) element.classList.add("is-selected");
      renderInspectorDetail(node);
    }

    function appendTraceStep(container, node) {
      const item = document.createElement("div");
      item.className = "trace-step";
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");
      const head = document.createElement("div");
      head.className = "trace-step-head";
      const title = document.createElement("div");
      title.className = "trace-step-title";
      title.textContent = node.title;
      const meta = document.createElement("div");
      meta.className = "trace-step-meta";
      meta.textContent = node.meta || "";
      head.append(title, meta);
      item.append(head);
      if (node.preview) {
        const preview = document.createElement("div");
        preview.className = "trace-step-preview";
        preview.textContent = node.preview;
        item.append(preview);
      }
      item.addEventListener("click", () => selectTraceNode(node, item));
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectTraceNode(node, item);
        }
      });
      container.appendChild(item);
      return item;
    }

    function appendToolGroup(container, group) {
      const item = document.createElement("div");
      item.className = "tool-group-card";
      item.dataset.kind = group.kind;
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");
      const head = document.createElement("div");
      head.className = "tool-group-head";
      const title = document.createElement("div");
      title.className = "tool-group-title";
      title.textContent = group.title;
      const meta = document.createElement("div");
      meta.className = "tool-group-meta";
      meta.textContent = group.meta || "";
      head.append(title, meta);
      item.append(head);
      if (group.preview) {
        const preview = document.createElement("div");
        preview.className = "tool-group-preview";
        preview.textContent = group.preview;
        item.append(preview);
      }
      const rowPayload = {
        ...(group.call?.detail?.arguments || {}),
        ...(group.resultCount ? {result_count: group.resultCount} : {}),
        ...(group.resultPayload?.duration_seconds ? {duration_seconds: group.resultPayload.duration_seconds} : {}),
      };
      appendJsonFieldRows(item, rowPayload);
      item.addEventListener("click", () => selectTraceNode(group, item));
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectTraceNode(group, item);
        }
      });
      container.appendChild(item);
      return item;
    }

    function appendSourceCard(container, source) {
      const item = document.createElement("div");
      item.className = "source-card";
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");
      const head = document.createElement("div");
      head.className = "source-card-head";
      const title = document.createElement("a");
      title.className = "source-card-title";
      title.href = source.url || "#";
      title.target = "_blank";
      title.rel = "noreferrer";
      title.textContent = source.title;
      const meta = document.createElement("div");
      meta.className = "source-card-meta";
      meta.textContent = compactMeta([source.host, source.query ? `query: ${source.query}` : ""]);
      head.append(title, meta);
      item.append(head);
      if (source.snippet) {
        const snippet = document.createElement("div");
        snippet.className = "source-card-snippet";
        snippet.textContent = source.snippet;
        item.append(snippet);
      }
      item.addEventListener("click", (event) => {
        if (event.target.tagName !== "A") selectTraceNode(source, item);
      });
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectTraceNode(source, item);
        }
      });
      container.appendChild(item);
      return item;
    }

    function appendEmptyState(message) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = message;
      agentLoopEvents.appendChild(empty);
    }

    function appendTimelineChildren(container, children) {
      if (!children?.length) return;
      const list = document.createElement("div");
      list.className = "timeline-children";
      for (const child of children.filter(Boolean)) {
        appendTimelineStep(list, child);
      }
      container.appendChild(list);
    }

    function timelineRawInfoNode(node) {
      return {
        ...node,
        title: `${node.title} raw payload`,
        detail: {},
        raw: node.raw,
      };
    }

    function appendTimelineStep(container, node, defaultOpen = false) {
      const item = document.createElement("div");
      item.className = "timeline-step trace-step";
      item.dataset.kind = node.kind || "";
      if (node.status) item.dataset.status = node.status;
      if (node.tool_call_id) item.dataset.toolCallId = node.tool_call_id;
      if (node.turn_id) item.dataset.turnId = String(node.turn_id);
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");

      const head = document.createElement("div");
      head.className = "timeline-step-head";
      const title = document.createElement("div");
      title.className = "timeline-step-title";
      const titleLabel = document.createElement("span");
      titleLabel.className = "timeline-title-label";
      titleLabel.textContent = node.title;
      title.appendChild(titleLabel);
      if (node.turn_id) {
        const turnBadge = document.createElement("span");
        turnBadge.className = "timeline-turn-badge";
        turnBadge.textContent = String(node.turn_id);
        title.prepend(turnBadge);
      }
      if (node.raw && (node.kind === "llm_input" || node.kind === "llm_output")) {
        const rawButton = document.createElement("button");
        rawButton.className = "timeline-info-button";
        rawButton.type = "button";
        rawButton.textContent = "i";
        rawButton.setAttribute("aria-label", `Show full ${node.title} payload`);
        rawButton.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          selectTraceNode(timelineRawInfoNode(node), rawButton);
        });
        title.appendChild(rawButton);
      }
      const meta = document.createElement("div");
      meta.className = "timeline-step-meta";
      meta.textContent = node.meta || "";
      const headTrailing = document.createElement("div");
      headTrailing.className = "timeline-head-trailing";
      headTrailing.appendChild(meta);
      head.append(title, headTrailing);
      item.append(head);

      if (node.preview) {
        const preview = document.createElement("div");
        preview.className = "timeline-step-preview";
        preview.textContent = node.preview;
        item.append(preview);
      }

      const detail = document.createElement("div");
      detail.className = "timeline-step-detail";
      detail.hidden = !defaultOpen;
      appendJsonFieldRows(detail, node.fields);
      if (node.detail && Object.keys(compactJsonRows(node.detail)).length) {
        detail.appendChild(renderStructuredValue(node.detail, {maxItems: 8, maxDepth: 2}));
      }
      appendTimelineChildren(detail, node.children);
      item.append(detail);

      const toggle = () => {
        detail.hidden = !detail.hidden;
        item.classList.toggle("is-selected", !detail.hidden);
      };
      item.addEventListener("click", (event) => {
        if (event.target.tagName === "A") return;
        if (event.target.closest(".timeline-step") !== item) return;
        toggle();
      });
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
      container.appendChild(item);
      return item;
    }

    function loopPurpose(turn) {
      const toolGroups = turn.toolGroups || [];
      if (toolGroups.some((group) => group.tool_name === "web_search")) return "Search";
      if (toolGroups.length) return "Action";
      if (turn.finish_reason === "stop") return "Answer";
      if (turn.finish_reason === "tool_calls") return "Tool decision";
      return "Model";
    }

    function thoughtSummary(turn, lastTurn, model) {
      const response = turn.llmOutput?.detail?.response || {};
      const toolCalls = response.tool_calls || [];
      if (toolCalls.length) {
        const tools = toolCalls
          .map((call) => call.function?.name || call.name || "tool")
          .filter(Boolean)
          .join(", ");
        return tools ? `Model chose to call ${tools} before answering.` : "Model chose to call a tool before answering.";
      }
      if (turn.toolGroups?.length) {
        return "Tool work was selected and its observation will be used in a later loop.";
      }
      if (turn === lastTurn && model.finalNode) {
        return model.sources.length
          ? "Model generated the final answer from the conversation and observed sources."
          : "Model generated the final answer from the conversation context.";
      }
      if (turn.finish_reason === "length") {
        return "Model hit the output limit before completing this turn.";
      }
      if (turn.finish_reason) {
        return `Model finished this turn with ${turn.finish_reason}.`;
      }
      return "Model is processing this turn.";
    }

    function stageNode({id, kind, title, meta = "", preview = "", fields = {}, detail = {}, raw = null, status = "", children = [], tool_call_id = "", turn_id = null}) {
      return {id, kind, title, meta, preview, fields, detail, raw, status, children, tool_call_id, turn_id};
    }

    function usageFields(usage) {
      if (!usage) return {};
      return {
        prompt_tokens: usage.prompt_tokens || 0,
        completion_tokens: usage.completion_tokens || 0,
        total_tokens: usage.total_tokens || 0,
      };
    }

    function observationPreview(group) {
      const sources = group.sources || [];
      const sourceLines = sources.slice(0, 3).map((source, index) => `${index + 1}. ${source.title}`).filter(Boolean);
      if (sourceLines.length) return sourceLines.join("\\n");
      if (group.progress?.length) return group.progress.map((item) => item.title || item.preview).filter(Boolean).join("\\n");
      return group.result ? group.result.preview : group.preview;
    }

    function messageRoleList(messages) {
      return (messages || []).map((message) => message.role).filter(Boolean).join(" → ");
    }

    function toolCallSummary(response) {
      const calls = response?.tool_calls || [];
      return calls.map((call) => {
        const name = call.function?.name || call.name || "tool";
        const args = parseMaybeJson(call.function?.arguments || call.arguments || "");
        if (args && typeof args === "object" && args.query) return `${name}(${args.query})`;
        return name;
      }).join(", ");
    }

    function toolResultSummary(content) {
      const payload = normalizedResultPayload(content);
      if (!Object.keys(payload).length) return "";
      const query = payload.query ? ` for "${payload.query}"` : "";
      const resultCount = typeof payload.result_count === "number"
        ? payload.result_count
        : Array.isArray(payload.results)
          ? payload.results.length
          : null;
      if (resultCount !== null) return `tool result: ${resultCount} result${resultCount === 1 ? "" : "s"}${query}`;
      if (payload.message) return previewValue(payload.message, 220);
      return "";
    }

    function lastMessageSummary(messages) {
      const lastMessage = (messages || [])[(messages || []).length - 1] || {};
      if (!lastMessage.role) return "";
      if (lastMessage.role === "tool") {
        return toolResultSummary(lastMessage.content) || "tool result";
      }
      if (lastMessage.tool_calls?.length) {
        return `assistant tool request: ${toolCallSummary(lastMessage)}`;
      }
      const content = typeof lastMessage.content === "string"
        ? lastMessage.content
        : JSON.stringify(lastMessage.content || "");
      const toolSummary = toolResultSummary(content);
      if (toolSummary) return toolSummary;
      return previewValue(`${lastMessage.role}: ${content}`, 260);
    }

    function toolNameList(tools) {
      return (tools || [])
        .map((tool) => tool.function?.name || tool.name || "")
        .filter(Boolean)
        .join(", ");
    }

    function normalizeCitationUrl(value) {
      let text = String(value || "");
      while (text && ".,;:!?*_)]}>".includes(text[text.length - 1])) {
        text = text.slice(0, -1);
      }
      return text;
    }

    function citedLinkCount(text) {
      const content = text || "";
      const markdownPattern = new RegExp("\\\\[[^\\\\]]+\\\\]\\\\((https?:\\\\/\\\\/[^\\\\s)]+)\\\\)", "g");
      const markdownUrls = [];
      let match = markdownPattern.exec(content);
      while (match) {
        markdownUrls.push(normalizeCitationUrl(match[1]));
        match = markdownPattern.exec(content);
      }
      const contentWithoutMarkdown = content.replace(markdownPattern, "");
      const bareLinks = contentWithoutMarkdown.match(new RegExp("https?:\\\\/\\\\/[^\\\\s)]+", "g")) || [];
      return new Set([...markdownUrls, ...bareLinks.map(normalizeCitationUrl)]).size;
    }

    function llmInputPreview(payload) {
      const detail = payload?.detail || {};
      const messages = detail.messages || [];
      const roles = messageRoleList(messages);
      const lastMessage = lastMessageSummary(messages);
      return previewValue([
        roles ? `context: ${roles}` : "",
        lastMessage ? `latest: ${lastMessage}` : "",
      ].filter(Boolean).join("\\n"), 260);
    }

    function sourceListSummary(sources) {
      return (sources || [])
        .slice(0, 3)
        .map((source, index) => {
          const host = source.host || hostLabel(source.url || "");
          return `${index + 1}. ${compactMeta([source.title, host])}`;
        })
        .filter(Boolean)
        .join("\\n");
    }

    function llmOutputPreview(payload) {
      const response = payload?.detail?.response || {};
      if (response.tool_calls?.length) {
        const calls = response.tool_calls
          .map((call) => call.function?.name || call.name || "tool")
          .join(", ");
        return `tool_calls: ${calls}`;
      }
      return previewValue(response.content || "model response", 260);
    }

    function buildLlmInputNode(turn) {
      const detail = turn.llmInput?.detail || {};
      return stageNode({
        id: `turn-${turn.turn_id}-llm-input`,
        kind: "llm_input",
        title: "LLM input",
        meta: compactMeta([
          detail.model || "",
          `${detail.message_count || detail.messages?.length || 0} messages`,
          `${detail.tool_count || detail.tools?.length || 0} tools`,
        ]),
        preview: llmInputPreview(turn.llmInput),
        fields: {
          model: detail.model || "",
          context: messageRoleList(detail.messages || []),
          tools_available: toolNameList(detail.tools || []),
          latest: lastMessageSummary(detail.messages || []),
          max_tokens: detail.max_tokens || "",
        },
        raw: turn.llmInput,
      });
    }

    function buildLlmOutputNode(turn) {
      const detail = turn.llmOutput.detail || {};
      const response = detail.response || {};
      return stageNode({
        id: `turn-${turn.turn_id}-llm-output`,
        kind: "llm_output",
        title: "LLM output",
        meta: compactMeta([
          response.tool_calls?.length ? `${response.tool_calls.length} tool ${response.tool_calls.length === 1 ? "call" : "calls"}` : response.content ? "content" : "",
          detail.finish_reason ? `finish ${detail.finish_reason}` : "",
        ]),
        preview: llmOutputPreview(turn.llmOutput),
        fields: {
          finish_reason: detail.finish_reason || "",
          tool_calls: toolCallSummary(response),
          content_summary: response.content ? previewValue(response.content, 320) : "",
        },
        raw: turn.llmOutput,
      });
    }

    function buildOutcomeNode(turn) {
      const nextStep = turn.toolGroups?.length
        ? turn.toolGroups.map((group) => group.tool_name || "tool").join(", ")
        : turn.finish_reason === "stop"
          ? "Response"
          : turn.finish_reason || "pending";
      return stageNode({
        id: `turn-${turn.turn_id}-outcome`,
        kind: "outcome",
        title: "Outcome",
        meta: compactMeta([
          turn.finish_reason ? `finish ${turn.finish_reason}` : "pending",
          turn.usage ? `${turn.usage.total_tokens} tokens` : "",
        ]),
        preview: compactMeta([
          turn.finish_reason ? `LLM returned ${turn.finish_reason}` : "Waiting for model outcome",
          turn.toolGroups?.length ? `${turn.toolGroups.length} next ${turn.toolGroups.length === 1 ? "action" : "actions"}` : "",
        ]),
        fields: {
          finish_reason: turn.finish_reason || "pending",
          next_step: nextStep,
          ...usageFields(turn.usage),
        },
        raw: turn.raw_events,
      });
    }

    function buildThoughtStep(turn, lastTurn, model) {
      const thoughtSummaryText = thoughtSummary(turn, lastTurn, model);
      const thoughtChildren = [];
      if (turn.llmInput) thoughtChildren.push(buildLlmInputNode(turn));
      if (turn.llmOutput) thoughtChildren.push(buildLlmOutputNode(turn));
      thoughtChildren.push(buildOutcomeNode(turn));
      return stageNode({
        id: `turn-${turn.turn_id}-thought`,
        kind: "thought",
        title: "Thought",
        meta: "reasoning flow",
        preview: previewValue(thoughtSummaryText, 280),
        raw: turn.raw_events.filter((event) => event.stage === "model_note"),
        children: thoughtChildren,
        turn_id: turn.turn_id,
      });
    }

    function buildActionStep(turn, group) {
      const args = group.call?.detail?.arguments || {};
      return stageNode({
        id: `turn-${turn.turn_id}-action-${group.tool_call_id || "tool"}`,
        kind: "action",
        title: "Action",
        meta: group.tool_name || "tool",
        preview: group.tool_name === "web_search" && args.query ? `web_search · ${args.query}` : group.preview,
        fields: {
          tool: group.tool_name,
          ...args,
        },
        raw: group.call?.raw || group.raw,
        tool_call_id: group.tool_call_id,
        turn_id: turn.turn_id,
      });
    }

    function buildObservationStep(turn, group) {
      const status = group.result?.detail?.is_error ? "failed" : group.result ? "success" : "running";
      const duration = group.resultPayload?.duration_seconds;
      const topSources = sourceListSummary(group.sources || []);
      return stageNode({
        id: `turn-${turn.turn_id}-observation-${group.tool_call_id || "tool"}`,
        kind: "observation",
        title: "Observation",
        meta: compactMeta([
          status,
          group.resultCount ? `${group.resultCount} results` : "",
          duration ? `${duration}s` : "",
        ]),
        preview: observationPreview(group),
        fields: {
          status,
          result_count: group.resultCount || 0,
          duration_seconds: duration || "",
          top_sources: topSources,
        },
        raw: group.raw,
        status,
        tool_call_id: group.tool_call_id,
        turn_id: turn.turn_id,
      });
    }

    function buildResponseStep(model) {
      return stageNode({
        id: "response",
        kind: "response",
        title: "Response",
        meta: compactMeta([
          "answer generated",
          model.stats.resultCount ? `${model.stats.resultCount} search results observed` : "",
        ]),
        preview: model.finalNode.preview,
        fields: {
          status: "answer generated",
          search_results_observed: model.stats.resultCount || 0,
          cited_links: citedLinkCount(model.finalNode.preview),
          total_tokens: model.stats.tokens || 0,
        },
        raw: model.finalNode.raw,
      });
    }

    function buildAgentStepSequence(model) {
      const steps = [];
      const inputText = model.inputNode?.preview || model.summary.input || "";
      steps.push(stageNode({
        id: "step-input",
        kind: "input",
        title: "Input",
        meta: "user request",
        preview: inputText,
        fields: {
          input: model.inputNode?.detail?.input || inputText,
        },
        raw: model.inputNode.raw,
      }));

      const lastTurn = model.turns[model.turns.length - 1] || null;
      for (const turn of model.turns) {
        steps.push(buildThoughtStep(turn, lastTurn, model));
        for (const group of turn.toolGroups || []) {
          steps.push(buildActionStep(turn, group));
          steps.push(buildObservationStep(turn, group));
        }
      }

      if (model.finalNode) steps.push(buildResponseStep(model));

      if (model.terminalNode) {
        steps.push(stageNode({
          id: "terminal",
          kind: model.terminalNode.kind,
          title: model.terminalNode.title,
          meta: model.terminalNode.meta,
          preview: model.terminalNode.preview,
          fields: model.terminalNode.detail,
          detail: model.terminalNode.detail,
          raw: model.terminalNode.raw,
          status: model.terminalNode.kind,
        }));
      }
      return steps;
    }

    function renderOverview(model) {
      agentLoopDetail.hidden = true;
      const card = document.createElement("div");
      card.className = "run-summary-card";
      const title = document.createElement("div");
      title.className = "run-summary-title";
      title.textContent = `${model.summary.run_id || "run"} · ${model.statusLabel}`;
      const meta = document.createElement("div");
      meta.className = "run-summary-meta";
      meta.textContent = compactMeta([
        `${model.stats.modelTurns} model ${model.stats.modelTurns === 1 ? "turn" : "turns"}`,
        model.stats.webSearches ? `${model.stats.webSearches} web ${model.stats.webSearches === 1 ? "search" : "searches"}` : "",
        model.stats.resultCount ? `${model.stats.resultCount} results` : "",
        model.summary.finish_reason ? `finish ${model.summary.finish_reason}` : "",
      ]);
      const stats = document.createElement("div");
      stats.className = "run-stat-grid";
      [
        ["Turns", model.stats.modelTurns],
        ["Tools", model.stats.toolCalls],
        ["Sources", model.sources.length],
        ["Tokens", model.stats.tokens],
      ].forEach(([label, value]) => {
        const stat = document.createElement("div");
        stat.className = "run-stat";
        const statValue = document.createElement("div");
        statValue.className = "run-stat-value";
        statValue.textContent = String(value || 0);
        const statLabel = document.createElement("div");
        statLabel.className = "run-stat-label";
        statLabel.textContent = label;
        stat.append(statValue, statLabel);
        stats.appendChild(stat);
      });
      card.append(title, meta, stats);
      agentLoopEvents.appendChild(card);

      const pathTitle = document.createElement("div");
      pathTitle.className = "trace-section-title";
      pathTitle.textContent = "Path";
      agentLoopEvents.appendChild(pathTitle);
      const path = document.createElement("ol");
      path.className = "run-path";
      for (const itemText of model.pathItems) {
        const item = document.createElement("li");
        item.textContent = itemText;
        path.appendChild(item);
      }
      agentLoopEvents.appendChild(path);

      if (model.sources.length) {
        const sourcesTitle = document.createElement("div");
        sourcesTitle.className = "trace-section-title";
        sourcesTitle.textContent = "Sources";
        agentLoopEvents.appendChild(sourcesTitle);
        model.sources.slice(0, 3).forEach((source) => appendSourceCard(agentLoopEvents, source));
      }
    }

    function renderSteps(model) {
      agentLoopDetail.hidden = true;
      const timeline = document.createElement("div");
      timeline.className = "reasoning-timeline";
      for (const step of buildAgentStepSequence(model)) {
        appendTimelineStep(timeline, step);
      }
      agentLoopEvents.appendChild(timeline);
    }

    function renderSources(model) {
      agentLoopDetail.hidden = true;
      if (!model.sources.length) {
        appendEmptyState("No web sources were returned for this run.");
        return;
      }
      model.sources.forEach((source) => appendSourceCard(agentLoopEvents, source));
    }

    function renderRaw(model) {
      agentLoopDetail.hidden = true;
      const raw = document.createElement("pre");
      raw.className = "raw-payload";
      raw.textContent = JSON.stringify({summary: model.summary, trace: model.trace}, null, 2);
      agentLoopEvents.appendChild(raw);
    }

    function renderInspector(model) {
      agentLoopEvents.innerHTML = "";
      updateTraceTabs();
      if (activeTraceTab === "steps") renderSteps(model);
      else if (activeTraceTab === "sources") renderSources(model);
      else if (activeTraceTab === "raw") renderRaw(model);
      else renderOverview(model);
    }

    function renderTraceCapsule(assistantView, model) {
      if (!assistantView.preserveActivityPath) {
        assistantView.extras.innerHTML = "";
        assistantView.activityPath = null;
        assistantView.pathItems = new Map();
      } else if (assistantView.traceCapsule) {
        assistantView.traceCapsule.remove();
      }
      const capsule = document.createElement("button");
      capsule.className = "trace-capsule";
      capsule.type = "button";
      const dot = document.createElement("span");
      dot.className = "trace-capsule-dot";
      const label = document.createElement("span");
      label.textContent = buildTraceCapsule(model.summary);
      capsule.append(dot, label);
      capsule.addEventListener("click", (event) => {
        event.stopPropagation();
        renderAssistantTrace(assistantView);
      });
      assistantView.traceCapsule = capsule;
      assistantView.extras.appendChild(capsule);
    }

    function renderAssistantTrace(assistantView) {
      activeAssistantView = assistantView;
      workspaceGrid.classList.add("has-trace");
      agentLoopPanel.hidden = false;
      agentLoopDetail.hidden = true;
      const model = buildRunDetailsModel(assistantView);
      loopStatus.textContent = model.statusLabel;
      renderInspector(model);
    }

    function bindAssistantDetail(assistantView, payload) {
      assistantView.completePayload = payload;
      const model = buildRunDetailsModel(assistantView);
      renderTraceCapsule(assistantView, model);
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
      assistantView.textStarted = false;
      assistantView.preserveActivityPath = false;
      assistantView.pathItems = new Map();
      setAssistantPlaceholder(assistantView, "Thinking...");
      appendAssistantPathItem(assistantView, {key: "input", label: "Understanding request"});
      let assistantText = "";
      currentAbortController = new AbortController();
      sendButton.disabled = true;
      stopButton.disabled = false;
      status.textContent = "Running";
      loopStatus.textContent = "Running";

      function handlePayload(payload) {
        if (payload.type === "assistant_delta") {
          assistantText += payload.text || "";
          assistantView.textStarted = true;
          clearAssistantPlaceholder(assistantView);
          assistantView.content.textContent = assistantText;
          scrollToBottom();
          return;
        }
        assistantTrace.push(payload);
        if (payload.type === "trace") {
          updateAssistantPathFromTrace(assistantView, payload);
          if (payload.stage === "tool_call") {
            const placeholder = payload.tool_name === "web_search" ? "Searching web..." : `Using ${payload.tool_name || "tool"}...`;
            setAssistantPlaceholder(assistantView, placeholder);
          } else if (payload.stage === "tool_progress" && payload.progress_type === "query_update") {
            setAssistantPlaceholder(assistantView, "Searching web...");
          } else if (payload.stage === "tool_result") {
            setAssistantPlaceholder(assistantView, "Reading tool result...");
          } else if (payload.stage === "model_note") {
            setAssistantPlaceholder(assistantView, "Thinking...");
          }
          return;
        }
        if (payload.type === "usage") {
          tokenCount.textContent = payload.tokens ?? tokenCount.textContent;
          return;
        }
        if (payload.type === "complete") {
          if (!assistantText && payload.reply) {
            assistantText = payload.reply;
            assistantView.textStarted = true;
            clearAssistantPlaceholder(assistantView);
            assistantView.content.textContent = assistantText;
          } else if (!assistantText) {
            assistantView.textStarted = true;
            assistantView.preserveActivityPath = true;
            clearAssistantPlaceholder(assistantView);
            assistantView.content.textContent = "No response returned.";
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
          assistantView.textStarted = true;
          assistantView.preserveActivityPath = true;
          clearAssistantPlaceholder(assistantView);
          assistantView.content.textContent = "Run stopped by user.";
          bindAssistantDetail(assistantView, stopPayload);
          status.textContent = "Stopped";
          loopStatus.textContent = "Stopped";
          return;
        }
        assistantView.textStarted = true;
        assistantView.preserveActivityPath = true;
        clearAssistantPlaceholder(assistantView);
        assistantView.content.textContent = "No response returned.";
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
      clearConversationView();
      input.focus();
    });

    modeButtons.forEach((button) => {
      button.addEventListener("click", async () => {
        const nextMode = button.dataset.modeOption;
        if (!nextMode || nextMode === activeMode || currentAbortController) return;
        modeButtons.forEach((modeButton) => { modeButton.disabled = true; });
        status.textContent = "Switching";
        try {
          const response = await fetch("/api/mode", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({mode: nextMode}),
          });
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || "Mode switch failed");
          setActiveMode(payload.mode || nextMode);
          clearConversationView();
        } catch (error) {
          status.textContent = "Error";
          appendMessage("error", error.message || "Mode switch failed");
        } finally {
          modeButtons.forEach((modeButton) => { modeButton.disabled = false; });
          input.focus();
        }
      });
    });

    stopButton.addEventListener("click", async () => {
      stopButton.disabled = true;
      status.textContent = "Stopping";
      loopStatus.textContent = "Stopping";
      if (currentAbortController) currentAbortController.abort();
      await fetch("/api/cancel", {method: "POST"});
    });

    traceTabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        activeTraceTab = tab.dataset.traceTab || "overview";
        if (activeAssistantView) renderAssistantTrace(activeAssistantView);
        else updateTraceTabs();
      });
    });

    loopDetailFilter.addEventListener("input", () => {
      if (selectedInspectorPayload) renderInspectorPayload(selectedInspectorPayload, loopDetailFilter.value);
    });

    copyPayloadButton.addEventListener("click", () => {
      copyInspectorPayload();
    });

    closeTraceButton.addEventListener("click", () => {
      hideAssistantTrace();
      input.focus();
    });

    setActiveMode(activeMode);
    input.focus();
  </script>
</body>
</html>"""
    return document.replace("__TOOL_MODE__", html.escape(tool_mode))


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
    if event.type == "llm_input":
        messages = event.llm_messages or []
        tools = event.llm_tools or []
        detail = {
            "model": event.llm_model or "",
            "messages": messages,
            "tools": tools,
            "max_tokens": event.llm_max_tokens,
            "message_count": len(messages),
            "tool_count": len(tools),
        }
        return {
            "type": "trace",
            "stage": "llm_input",
            "label": "LLM input",
            "detail": detail,
            "run_id": run_id,
            "turn_id": event.turn or turn_id,
        }
    if event.type == "llm_output":
        response = event.llm_response or {}
        detail = {
            "model": event.llm_model or "",
            "response": response,
            "finish_reason": event.finish_reason or "",
        }
        return {
            "type": "trace",
            "stage": "llm_output",
            "label": "LLM output",
            "detail": detail,
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
    tool_mode: ToolMode = "chatbot"
    chatbot_factory: Callable[[ToolMode], Chatbot] | None = None

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

    def set_mode(self, mode: str) -> dict[str, str | bool]:
        if mode not in SWITCHABLE_TOOL_MODES:
            raise ValueError(f"Unknown tool mode: {mode}")
        with self._lock:
            if mode != self.tool_mode:
                if self.chatbot_factory is None:
                    raise ValueError("Mode switching is not configured")
                self.chatbot = self.chatbot_factory(mode)  # type: ignore[arg-type]
                self.tool_mode = mode  # type: ignore[assignment]
                self._run_counter = 0
            else:
                self.chatbot.reset()
        return {"ok": True, "mode": self.tool_mode}


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
                    render_index_html(tool_mode=app.tool_mode).encode("utf-8"),
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
            if self.path == "/api/mode":
                try:
                    payload = self._read_json()
                    self._send_json(HTTPStatus.OK, app.set_mode(str(payload.get("mode", ""))))
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
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
    tools: ToolMode = "chatbot",
    open_browser: bool = True,
) -> ThreadingHTTPServer:
    """Create and run the local chatbot web server."""
    def make_chatbot(tool_mode: ToolMode) -> Chatbot:
        return build_chatbot(
            provider=provider,
            model=model,
            base_url=base_url,
            system_prompt=system_prompt,
            tools=tool_mode,
        )

    chatbot = make_chatbot(tools)
    app = ChatbotWebApp(chatbot, tool_mode=tools, chatbot_factory=make_chatbot)
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
    parser.add_argument(
        "--tools",
        choices=["none", "chatbot", "workspace", "all"],
        default="chatbot",
    )
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
