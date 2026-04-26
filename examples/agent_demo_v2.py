"""
End-to-end demo: Claude-powered agent with EPHEMERAL code execution.

Same as agent_demo.py, but uses the new `ephemeral.log` decorator instead of
a hand-rolled `send_context` + `requests.post`. The SDK now streams each
(user_input, ai_output) pair to the static provisioner endpoint automatically.

Usage:
    cd caltech
    pip install -e sdk/ anthropic python-dotenv
    python examples/agent_demo_v2.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file)

import anthropic
import ephemeral

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("EPHEMERAL_API_BASE_URL", "http://localhost:8001").rstrip("/")
SESSION_ID = os.environ.get("EPHEMERAL_SESSION_ID", "sess-demo-2048")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5"

if not ANTHROPIC_API_KEY:
    print("Error: ANTHROPIC_API_KEY is not set.")
    sys.exit(1)

# Bind the SDK's session id to the orchestrator's session id so context POSTs
# from `ephemeral.log` land on the same provisioner session this demo uses.
session = ephemeral.Session(session_id=SESSION_ID)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Wrap the LLM call: every turn, the latest (user, assistant) pair is streamed
# to the static context endpoint so the provisioner can pre-warm.
client.messages.create = ephemeral.log(session=session)(client.messages.create)

# ---------------------------------------------------------------------------
# Tool schema exposed to Claude
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "execute_code",
        "description": (
            "Execute Python code in a managed sandbox container. "
            "Returns stdout, stderr, and exit code. "
            "Always use this tool to run computations — never calculate manually."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python source code to execute.",
                },
                "resource_tier": {
                    "type": "string",
                    "enum": ["light", "medium", "heavy"],
                    "description": "Compute tier hint. Omit to auto-detect.",
                },
            },
            "required": ["code"],
        },
    }
]


def call_execute_code(code: str, resource_tier: str | None = None) -> dict:
    payload: dict = {"code": code, "language": "python", "session_id": SESSION_ID}
    if resource_tier:
        payload["resource_tier"] = resource_tier

    resp = requests.post(f"{BASE_URL}/api/execute", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def format_exec_result(result: dict) -> str:
    parts = []
    if result.get("stdout"):
        parts.append(f"stdout:\n{result['stdout']}")
    if result.get("stderr"):
        parts.append(f"stderr:\n{result['stderr']}")
    parts.append(f"exit_code: {result['exit_code']}")
    parts.append(
        f"[matched: {result.get('matched', '?')} | "
        f"profile: {result.get('profile')} | "
        f"tier: {result.get('resource_tier')} | "
        f"exec: {result.get('duration_ms')}ms | "
        f"total: {result.get('total_ms')}ms]"
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Claude agent loop
# ---------------------------------------------------------------------------

@ephemeral.track(model=MODEL, session=session)
def run_agent(task: str) -> list[dict]:
    messages: list[dict] = [{"role": "user", "content": task}]

    system = "You are a data analysis agent with access to a Python sandbox. "

    while True:
        print("\r[claude] thinking...", end="", flush=True)

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        print(f"\r[claude] stop_reason: {response.stop_reason}        ")

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text = " ".join(
                block.text for block in response.content
                if hasattr(block, "text")
            )
            print(f"\n[agent answer]\n{text}")
            break

        if response.stop_reason != "tool_use":
            print(f"\n[unexpected stop_reason: {response.stop_reason}]")
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            print(f"\n[tool call] {block.name}({json.dumps(block.input, indent=2)})")

            if block.name == "execute_code":
                _t0 = time.perf_counter()
                result = call_execute_code(
                    block.input["code"],
                    resource_tier=block.input.get("resource_tier"),
                )
                _exec_ms = (time.perf_counter() - _t0) * 1000
                tool_result_str = format_exec_result(result)
                print(f"\n[execution result] ({_exec_ms:.0f}ms round-trip)\n{tool_result_str}")
            else:
                tool_result_str = f"Unknown tool: {block.name}"

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": tool_result_str,
            })

        messages.append({"role": "user", "content": tool_results})

    return messages


if __name__ == "__main__":
    task = (
        "I have a dataset: [4, 7, 13, 2, 1, 9, 3, 15, 6, 11]. "
        "Use execute_code to run Python that computes the mean, median, standard deviation, "
        "and prints a simple ASCII histogram. Do not calculate by hand."
    )

    print("=" * 60)
    print("EPHEMERAL end-to-end demo (Claude) — v2 (ephemeral.log)")
    print(f"Session: {SESSION_ID}")
    print(f"VM:      {BASE_URL}")
    print(f"Model:   {MODEL}")
    print("=" * 60)

    print("\n[step 1] Running Claude agent — context streams automatically per turn...")
    conversation = run_agent(task)

    ephemeral.flush()
    print("\n[done] Telemetry flushed.")
