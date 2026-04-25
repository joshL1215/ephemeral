"""
End-to-end demo: Claude-powered agent with EPHEMERAL code execution.

Flow:
  1. Send a context event to prime the provisioner (so it pre-warms a container)
  2. Run a Claude agent that has access to execute_code
  3. Agent reasons about the task and calls execute_code
  4. Code runs on the VM in a pre-warmed container

Usage:
    cd caltech
    pip install -e sdk/ anthropic python-dotenv
    # Credentials are read from caltech/.env automatically
    python examples/agent_demo.py

Environment variables (can be set in caltech/.env):
    ANTHROPIC_API_KEY       Required. Anthropic API key.
    EPHEMERAL_API_BASE_URL  Orchestrator base URL. Defaults to http://localhost:8001
    EPHEMERAL_SESSION_ID    Session ID to use. Defaults to sess-demo-2048
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

# Load .env from the caltech/ root (one level up from examples/)
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

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def send_context(content: str) -> None:
    """Tell the provisioner what's coming so it can pre-warm."""
    resp = requests.post(
        f"{BASE_URL}/api/sessions/{SESSION_ID}/context",
        json={"content": content, "source": "agent"},
        timeout=10,
    )
    resp.raise_for_status()
    print(f"[provisioner] context accepted → {resp.json()}")


def call_execute_code(code: str, resource_tier: str | None = None) -> dict:
    """Call the orchestrator's /api/execute endpoint."""
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

@ephemeral.track(model=MODEL)
def run_agent(task: str) -> list[dict]:
    """Run a Claude agent that can call execute_code. Returns the full message history."""
    messages: list[dict] = [{"role": "user", "content": task}]

    system = (
        "You are a data analysis agent with access to a Python sandbox. "
        "You MUST use the execute_code tool to run all computations — never calculate manually. "
        "Write the code, run it, and report the output."
    )

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

        # Append assistant turn (full content block list)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Extract text and print final answer
            text = " ".join(
                block.text for block in response.content
                if hasattr(block, "text")
            )
            print(f"\n[agent answer]\n{text}")
            break

        if response.stop_reason != "tool_use":
            print(f"\n[unexpected stop_reason: {response.stop_reason}]")
            break

        # Handle tool calls
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    task = (
        "I have a dataset: [4, 7, 13, 2, 1, 9, 3, 15, 6, 11]. "
        "Use execute_code to run Python that computes the mean, median, standard deviation, "
        "and prints a simple ASCII histogram. Do not calculate by hand."
    )

    print("=" * 60)
    print("EPHEMERAL end-to-end demo (Claude)")
    print(f"Session: {SESSION_ID}")
    print(f"VM:      {BASE_URL}")
    print(f"Model:   {MODEL}")
    print("=" * 60)

    # Step 1: prime the provisioner
    print("\n[step 1] Sending context to provisioner...")
    send_context(
        "User wants to do statistical analysis on a numeric dataset — "
        "mean, median, std dev, and histogram visualization."
    )

    print("[step 1] Waiting 3s for provisioner to act...")
    time.sleep(3)

    # Step 2: run the agent
    print("\n[step 2] Running Claude agent...")
    conversation = run_agent(task)

    ephemeral.flush()
    print("\n[done] Telemetry flushed.")
