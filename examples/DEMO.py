"""
Terminal chatbot with code execution.
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

BASE_URL = os.environ.get("EPHEMERAL_API_BASE_URL", "http://localhost:8001").rstrip("/")
SESSION_ID = os.environ.get("EPHEMERAL_SESSION_ID", "sess-demo-2048")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5"

if not ANTHROPIC_API_KEY:
    print("Error: ANTHROPIC_API_KEY is not set.")
    sys.exit(1)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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


def send_context(content: str) -> None:
    resp = requests.post(
        f"{BASE_URL}/api/sessions/{SESSION_ID}/context",
        json={"content": content, "source": "agent"},
        timeout=10,
    )
    resp.raise_for_status()
    print(f"[provisioner] context accepted → {resp.json()}")


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


SYSTEM = (
    "You are a helpful assistant. You can chat naturally and answer questions directly. "
    "When the user asks you to run code, do math, or analyze data, use the execute_code tool. "
    "Otherwise, just respond conversationally."
)


def step(messages: list[dict]) -> list[dict]:
    """Run the agent loop until end_turn, mutating and returning messages."""
    while True:
        print("\r thinking...", end="", flush=True)

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
        )

        print(f"\r                                    ", end="", flush=True)
        messages.append({"role": "assistant", "content": response.content})
        ephemeral.log(messages=messages, model=MODEL)
        send_context(str(messages))

        if response.stop_reason == "end_turn":
            text = " ".join(
                block.text for block in response.content
                if hasattr(block, "text")
            )
            print(f"\nMe: {text}\n")
            return messages

        if response.stop_reason != "tool_use":
            return messages

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            print(f"\r running code...          ", end="", flush=True)

            if block.name == "execute_code":
                _t0 = time.perf_counter()
                result = call_execute_code(
                    block.input["code"],
                    resource_tier=block.input.get("resource_tier"),
                )
                _exec_ms = (time.perf_counter() - _t0) * 1000
                tool_result_str = format_exec_result(result)
                print(f"\r                                    ", end="", flush=True)
            else:
                tool_result_str = f"Unknown tool: {block.name}"

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": tool_result_str,
            })

        messages.append({"role": "user", "content": tool_results})
        ephemeral.log(messages=messages, model=MODEL)


if __name__ == "__main__":
    print("=" * 40)
    print("Chat with me!")
    print("Type 'exit' or Ctrl-C to quit.")
    print("=" * 40)

    time.sleep(3)

    messages: list[dict] = []

    try:
        while True:
            try:
                user_input = input("\nYou: ").strip()
            except EOFError:
                break

            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                break

            messages.append({"role": "user", "content": user_input})
            step(messages)
    except KeyboardInterrupt:
        pass
    finally:
        ephemeral.flush()
