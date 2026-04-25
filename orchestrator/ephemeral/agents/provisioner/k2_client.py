import asyncio
import json
import logging
import os
import re
from typing import Any

import httpx
from pydantic import BaseModel

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

_log = logging.getLogger("ephemeral.k2")

_BASE_URL = "https://api.k2think.ai/v1"
_MODEL = "MBZUAI-IFM/K2-Think-v2"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class K2Response(BaseModel):
    content: str | None
    reasoning_content: str | None
    tool_calls: list[ToolCall]


class K2Client:
    def __init__(self, api_key: str, base_url: str = _BASE_URL) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> K2Response:
        payload: dict[str, Any] = {
            "model": _MODEL,
            "messages": messages,
            "temperature": 1.0,
            "top_p": 1.0,
            "max_tokens": 16384,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        _log.debug("K2 request: %s", payload)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await self._post_with_retry(client, headers, payload)

        data = response.json()
        _log.debug("K2 response: %s", data)

        message = data["choices"][0]["message"]
        raw_content = message.get("content") or ""

        # K2 Think V2 embeds reasoning in <think>...</think> tags inside content
        reasoning_content = message.get("reasoning_content")
        if not reasoning_content:
            match = _THINK_RE.search(raw_content)
            if match:
                reasoning_content = match.group(1).strip()
        content = _THINK_RE.sub("", raw_content).strip() or None

        raw_tool_calls = message.get("tool_calls") or []
        tool_calls = []
        for tc in raw_tool_calls:
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=args,
            ))

        return K2Response(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
        )

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        payload: dict,
    ) -> httpx.Response:
        url = f"{self._base_url}/chat/completions"
        for attempt in range(2):
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code < 500 or attempt == 1:
                response.raise_for_status()
                return response
            _log.warning("K2 5xx on attempt %d, retrying in 1s", attempt + 1)
            await asyncio.sleep(1.0)
        raise RuntimeError("unreachable")


def k2_client_from_env() -> K2Client:
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ["K2_API_KEY"]
    return K2Client(api_key=api_key)
