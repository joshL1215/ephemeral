"""Minimal chatbot demo: Claude + Ephemeral code execution."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

import anthropic
import ephemeral
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

BASE_URL = os.environ.get("EPHEMERAL_API_BASE_URL", "http://localhost:8001").rstrip("/")
SESSION_ID = os.environ.get("EPHEMERAL_SESSION_ID", "sess-demo-2048")
MODEL = "claude-haiku-4-5"

_claude = anthropic.Anthropic()
_conversations: dict[str, list] = {}

TOOLS = [
    {
        "name": "execute_code",
        "description": (
            "Execute Python code in a managed sandbox. "
            "Returns stdout, stderr, and exit code. "
            "Always use this for computations — never calculate manually."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source code to execute."},
            },
            "required": ["code"],
        },
    }
]

SYSTEM = (
    "You are a helpful assistant with access to a Python sandbox. "
    "Use execute_code to run code when asked or to verify results."
)


def call_execute(code: str) -> str:
    resp = requests.post(
        f"{BASE_URL}/api/execute",
        json={"code": code, "language": "python", "session_id": SESSION_ID},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = []
    if data.get("stdout"):
        parts.append(f"stdout:\n{data['stdout']}")
    if data.get("stderr"):
        parts.append(f"stderr:\n{data['stderr']}")
    parts.append(f"exit_code: {data['exit_code']}")
    return "\n\n".join(parts)


@ephemeral.track(model=MODEL)
def run_agent(messages: list) -> list:
    while True:
        response = _claude.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return messages

        tool_results = []
        for block in response.content:
            if block.type == "tool_use" and block.name == "execute_code":
                result = call_execute(block.input["code"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})


HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Ephemeral Code Chat</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:monospace;background:#0d1117;color:#c9d1d9;height:100vh;display:flex;flex-direction:column}
  #log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
  .msg{max-width:82%;padding:10px 14px;border-radius:8px;white-space:pre-wrap;word-break:break-word;line-height:1.5}
  .user{align-self:flex-end;background:#1f6feb;color:#fff}
  .assistant{align-self:flex-start;background:#161b22;border:1px solid #30363d}
  .thinking{align-self:flex-start;color:#8b949e;font-style:italic}
  #bar{display:flex;padding:12px;gap:8px;border-top:1px solid #21262d}
  #input{flex:1;background:#161b22;border:1px solid #30363d;color:#c9d1d9;padding:8px 12px;border-radius:6px;font-family:monospace;font-size:14px}
  #send{background:#238636;color:#fff;border:none;padding:8px 18px;border-radius:6px;cursor:pointer;font-family:monospace;font-size:14px}
  #send:disabled{opacity:.4;cursor:default}
</style>
</head>
<body>
<div id="log"></div>
<div id="bar">
  <input id="input" placeholder="Ask anything — I can run Python for you..." autofocus>
  <button id="send">Send</button>
</div>
<script>
const log = document.getElementById('log');
const input = document.getElementById('input');
const send = document.getElementById('send');

function append(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}

async function submit() {
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  send.disabled = true;
  append('user', msg);
  const thinking = append('thinking', '…');
  try {
    const r = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg})
    });
    const data = await r.json();
    thinking.remove();
    append('assistant', data.reply ?? data.error ?? '(empty)');
  } catch(e) {
    thinking.remove();
    append('assistant', 'Error: ' + e);
  }
  send.disabled = false;
  input.focus();
}

send.onclick = submit;
input.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } };
</script>
</body>
</html>"""


async def homepage(request: Request):
    return HTMLResponse(HTML)


async def chat_endpoint(request: Request):
    body = await request.json()
    user_msg = body.get("message", "")

    history = _conversations.setdefault("default", [])
    history.append({"role": "user", "content": user_msg})

    try:
        updated = run_agent(list(history))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    reply = next(
        (
            " ".join(b.text for b in msg["content"] if hasattr(b, "text"))
            for msg in reversed(updated)
            if msg["role"] == "assistant"
        ),
        "",
    )
    history.append({"role": "assistant", "content": reply})
    return JSONResponse({"reply": reply})


app = Starlette(routes=[Route("/", homepage), Route("/chat", chat_endpoint, methods=["POST"])])
