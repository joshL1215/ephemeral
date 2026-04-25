"""MCP stdio server. Exposes execute_code as a tool that routes to Ephemeral's execution endpoint."""
from __future__ import annotations

import json
import os
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

_BASE_URL = os.environ.get("EPHEMERAL_API_BASE_URL", "https://api.ephemeral-ai.com")
_EXECUTE_URL = f"{_BASE_URL.rstrip('/')}/api/execute"
_TIMEOUT_S = 60

mcp = FastMCP("ephemeral")


def _api_key() -> str | None:
    return os.environ.get("EPHEMERAL_API_KEY")


def _session_id() -> str | None:
    return os.environ.get("EPHEMERAL_SESSION_ID")


@mcp.tool()
def execute_code(code: str, language: str = "python") -> str:
    """Execute code in an Ephemeral-managed container.

    Args:
        code: Source code to execute.
        language: Programming language (python, javascript, bash, etc.).

    Returns:
        stdout, stderr, and exit code as a formatted string.
    """
    api_key = _api_key()
    if not api_key:
        return "Error: EPHEMERAL_API_KEY environment variable is not set."

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "code": code,
        "language": language,
    }
    session_id = _session_id()
    if session_id:
        payload["session_id"] = session_id

    try:
        resp = requests.post(_EXECUTE_URL, json=payload, headers=headers, timeout=_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        return "Error: execution timed out."
    except requests.HTTPError as e:
        try:
            detail = e.response.json().get("error", e.response.text)
        except Exception:
            detail = str(e)
        return f"Error: {detail}"
    except requests.RequestException as e:
        return f"Error: {e}"

    stdout = data.get("stdout", "")
    stderr = data.get("stderr", "")
    exit_code = data.get("exit_code", 0)

    parts: list[str] = []
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    parts.append(f"exit_code: {exit_code}")
    return "\n\n".join(parts)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
