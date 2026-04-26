"""
Minimal chatbot web app: Claude + Ephemeral (via MCP stdio server).

Mirrors the agent_demo.py pattern (@ephemeral.track, ephemeral.flush) but
routes all execute_code calls through the MCP server subprocess rather than
hitting the orchestrator directly.

Usage:
    cd caltech
    sdk/demo/.venv/bin/python examples/chatbot.py
    # then open http://localhost:8080

Environment variables (read from caltech/.env automatically):
    ANTHROPIC_API_KEY       Required.
    EPHEMERAL_API_BASE_URL  Orchestrator base URL. Defaults to http://localhost:8001
    EPHEMERAL_SESSION_ID    Session ID. Defaults to sess-demo-2048
    EPHEMERAL_API_KEY       Optional. Passed to MCP server for auth.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import anthropic
import ephemeral
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

MODEL = "claude-haiku-4-5"
SESSION_ID = os.environ.get("EPHEMERAL_SESSION_ID", "sess-demo-2048")

_claude = anthropic.Anthropic()
_conversations: dict[str, list] = {}

# MCP session shared across all requests (kept alive via lifespan).
_mcp: ClientSession | None = None
# Tool schemas discovered from the MCP server on startup.
_mcp_tools: list[dict] = []


# ---------------------------------------------------------------------------
# MCP ↔ Claude tool schema bridge
# ---------------------------------------------------------------------------

def _mcp_tools_to_claude(tools) -> list[dict]:
    """Convert MCP ListToolsResult entries to Anthropic tool dicts."""
    result = []
    for t in tools:
        schema = t.inputSchema if hasattr(t, "inputSchema") else {}
        result.append({
            "name": t.name,
            "description": t.description or "",
            "input_schema": schema,
        })
    return result


# ---------------------------------------------------------------------------
# Agent loop (uses @ephemeral.track like agent_demo.py)
# ---------------------------------------------------------------------------

SYSTEM = (
    "You are a helpful assistant with access to a Python sandbox via execute_code. "
    "Use execute_code to run code when asked or to verify results."
)


async def run_agent(messages: list) -> list:
    """Agent loop mirroring agent_demo.py; uses ephemeral.log for telemetry."""
    start = time.time()
    try:
        while True:
            response = _claude.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM,
                tools=_mcp_tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason != "tool_use":
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                # Route all tool calls through the live MCP session.
                mcp_result = await _mcp.call_tool(block.name, block.input)
                content = "\n".join(
                    c.text for c in mcp_result.content if hasattr(c, "text")
                ) if mcp_result.content else ""
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                })

            messages.append({"role": "user", "content": tool_results})
    finally:
        # Mirror @ephemeral.track telemetry manually since track() is sync-only.
        ephemeral.log(messages, model=MODEL, metadata={"duration_ms": (time.time() - start) * 1000})

    return messages


# ---------------------------------------------------------------------------
# Starlette app with lifespan managing the MCP subprocess
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: Starlette):
    global _mcp, _mcp_tools

    env = os.environ.copy()
    env.setdefault("EPHEMERAL_SESSION_ID", SESSION_ID)

    server_params = StdioServerParameters(
        command=str(Path(__file__).parent.parent / "sdk" / "demo" / ".venv" / "bin" / "python3"),
        args=["-m", "ephemeral.mcp_server"],
        env=env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            _mcp = session
            _mcp_tools = _mcp_tools_to_claude(tools_result.tools)
            print(f"[mcp] connected — tools: {[t['name'] for t in _mcp_tools]}")
            yield

    _mcp = None
    _mcp_tools = []
    ephemeral.flush()
    print("[ephemeral] telemetry flushed")


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

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
        updated = await run_agent(list(history))
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


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", homepage),
        Route("/chat", chat_endpoint, methods=["POST"]),
    ],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
