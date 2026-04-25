"""
End-to-end demo: K2-powered agent with EPHEMERAL code execution.

Flow:
  1. Send a context event to prime the provisioner (so it pre-warms a container)
  2. Run a K2 agent that has access to execute_code
  3. Agent reasons about the task and calls execute_code
  4. Code runs on the VM in a pre-warmed container

Usage:
    cd caltech
    pip install -e sdk/
    K2_API_KEY=... EPHEMERAL_API_BASE_URL=http://45.32.227.231:8001 python examples/agent_demo.py

Environment variables:
    K2_API_KEY              Required. K2 Think API key.
    EPHEMERAL_API_BASE_URL  Orchestrator base URL. Defaults to http://localhost:8001
    EPHEMERAL_SESSION_ID    Session ID to use. Defaults to sess-demo-2048
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import requests
import ephemeral

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("EPHEMERAL_API_BASE_URL", "http://localhost:8001").rstrip("/")
SESSION_ID = os.environ.get("EPHEMERAL_SESSION_ID", "sess-demo-2048")
K2_API_KEY = os.environ.get("K2_API_KEY", "")
K2_BASE_URL = "https://api.k2think.ai/v1"
K2_MODEL = "MBZUAI-IFM/K2-Think-v2"

if not K2_API_KEY:
    print("Error: K2_API_KEY is not set.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Tool schema exposed to K2
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": (
                "Execute Python code in a managed sandbox container. "
                "Returns stdout, stderr, and exit code."
            ),
            "parameters": {
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
    parts.append(f"[matched: {result.get('matched', '?')} | profile: {result.get('profile')} | tier: {result.get('resource_tier')}]")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# K2 agent loop
# ---------------------------------------------------------------------------

@ephemeral.track(model=K2_MODEL)
def run_agent(task: str) -> list[dict]:
    """Run a K2 agent that can call execute_code. Returns the full conversation."""
    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "You are a data analysis agent with access to a Python sandbox. "
                "You MUST use the execute_code tool to run all computations — never calculate manually. "
                "Write the code, run it, and report the output. Do not explain your reasoning before calling the tool."
            ),
        },
        {"role": "user", "content": task},
    ]

    headers = {
        "Authorization": f"Bearer {K2_API_KEY}",
        "Content-Type": "application/json",
    }

    while True:
        tool_choice = "auto"

        payload = {
            "model": K2_MODEL,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": tool_choice,
            "temperature": 1.0,
            "top_p": 1.0,
            "max_tokens": 2048,
            "stream": True,
            "extra_body": {"chat_template_kwargs": {"reasoning_effort": "low"}},
        }

        # Spinner so we know K2 is working
        stop_spinner = threading.Event()
        def _spin():
            chars = "|/-\\"
            i = 0
            while not stop_spinner.is_set():
                print(f"\r[k2] thinking {chars[i % 4]}", end="", flush=True)
                i += 1
                time.sleep(0.15)
        spinner = threading.Thread(target=_spin, daemon=True)
        spinner.start()

        payload["stream"] = False
        resp = requests.post(
            f"{K2_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=180,
        )
        stop_spinner.set()
        print()
        resp.raise_for_status()
        data = resp.json()

        message = data["choices"][0]["message"]
        full_content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []

        # K2 embeds thinking before </think> in the content field
        think_match = re.search(r"^(.*?)</think>(.*)", full_content, re.DOTALL)
        if think_match:
            full_reasoning = think_match.group(1).strip()
            full_content = think_match.group(2).strip()
        else:
            full_reasoning = ""

        if full_reasoning:
            print(f"\n[k2 thinking]\n{full_reasoning[:500]}{'...' if len(full_reasoning) > 500 else ''}")

        assistant_msg: dict = {"role": "assistant", "content": full_content or None}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            print(f"\n[agent answer]\n{full_content}")
            break

        for tc in tool_calls:
            fn = tc["function"]
            args = fn["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            if isinstance(args, str):
                args = json.loads(args)
            print(f"\n[tool call] {fn['name']}({json.dumps(args, indent=2)})")

            if fn["name"] == "execute_code":
                _t0 = time.perf_counter()
                result = call_execute_code(
                    args["code"],
                    resource_tier=args.get("resource_tier"),
                )
                _exec_ms = (time.perf_counter() - _t0) * 1000
                tool_result = format_exec_result(result)
                print(f"\n[execution result] ({_exec_ms:.0f}ms end-to-end)\n{tool_result}")
            else:
                tool_result = f"Unknown tool: {fn['name']}"

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": tool_result,
            })

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
    print("EPHEMERAL end-to-end demo")
    print(f"Session: {SESSION_ID}")
    print(f"VM:      {BASE_URL}")
    print("=" * 60)

    # Step 1: prime the provisioner
    print("\n[step 1] Sending context to provisioner...")
    send_context(
        "User wants to do statistical analysis on a numeric dataset — "
        "mean, median, std dev, and histogram visualization."
    )

    # Give the provisioner a moment to start warming
    print("[step 1] Waiting 3s for provisioner to act...")
    time.sleep(3)

    # Step 2: run the agent
    print("\n[step 2] Running K2 agent...")
    conversation = run_agent(task)

    ephemeral.flush()
    print("\n[done] Telemetry flushed.")
