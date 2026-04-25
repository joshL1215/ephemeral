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
import sys
import time
import httpx
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
    resp = httpx.post(
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

    resp = httpx.post(f"{BASE_URL}/api/execute", json=payload, timeout=120)
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
                "You are a helpful data analysis agent. "
                "When asked to perform computations or analysis, write Python code and use "
                "the execute_code tool to run it. Always show the output to the user."
            ),
        },
        {"role": "user", "content": task},
    ]

    headers = {
        "Authorization": f"Bearer {K2_API_KEY}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=60) as client:
        while True:
            payload = {
                "model": K2_MODEL,
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
                "temperature": 1.0,
                "top_p": 1.0,
                "max_tokens": 8192,
                "extra_body": {"chat_template_kwargs": {"reasoning_effort": "low"}},
            }

            print("\n[k2] calling inference...")
            resp = client.post(f"{K2_BASE_URL}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            choice = data["choices"][0]
            message = choice["message"]
            messages.append(message)

            # Print reasoning if present
            reasoning = message.get("reasoning_content") or ""
            if not reasoning:
                content = message.get("content") or ""
                import re
                m = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
                if m:
                    reasoning = m.group(1).strip()
            if reasoning:
                print(f"\n[k2 reasoning]\n{reasoning[:400]}{'...' if len(reasoning) > 400 else ''}")

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                # Final answer
                final = message.get("content", "")
                print(f"\n[agent final answer]\n{final}")
                break

            # Dispatch tool calls
            for tc in tool_calls:
                fn = tc["function"]
                args = fn["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                if isinstance(args, str):
                    args = json.loads(args)  # K2 sometimes double-encodes
                print(f"\n[tool call] {fn['name']}({json.dumps(args, indent=2)})")

                if fn["name"] == "execute_code":
                    result = call_execute_code(
                        args["code"],
                        resource_tier=args.get("resource_tier"),
                    )
                    tool_result = format_exec_result(result)
                    print(f"\n[execution result]\n{tool_result}")
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
        "I have a dataset of numbers: [4, 7, 13, 2, 1, 9, 3, 15, 6, 11]. "
        "Please compute the mean, median, standard deviation, and plot a simple "
        "ASCII histogram of the values."
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
