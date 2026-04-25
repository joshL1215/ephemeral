import json
import time
from collections import deque
from typing import Literal

from pydantic import BaseModel


class ContextEvent(BaseModel):
    ts: float
    source: Literal["agent", "system"]
    content: str | dict

    @classmethod
    def from_text(cls, text: str, source: Literal["agent", "system"] = "agent") -> "ContextEvent":
        return cls(ts=time.time(), source=source, content=text)


class ContextWindow:
    def __init__(self, max_events: int = 30, max_chars: int = 8000) -> None:
        self._max_events = max_events
        self._max_chars = max_chars
        self._events: deque[ContextEvent] = deque()
        self._total_chars: int = 0

    def add(self, event: ContextEvent) -> None:
        text = self._event_chars(event)
        self._events.append(event)
        self._total_chars += len(text)

        # evict oldest until within both limits
        while (len(self._events) > self._max_events or self._total_chars > self._max_chars):
            evicted = self._events.popleft()
            self._total_chars -= len(self._event_chars(evicted))

    def render_for_prompt(self) -> str:
        lines = []
        for e in self._events:
            content = e.content if isinstance(e.content, str) else json.dumps(e.content)
            lines.append(f"[{e.source}] {content}")
        return "\n".join(lines)

    def recent_text(self) -> str:
        """Flat concatenation of all event content — used for heuristic scanning."""
        parts = []
        for e in self._events:
            if isinstance(e.content, str):
                parts.append(e.content)
            else:
                parts.append(json.dumps(e.content))
        return " ".join(parts).lower()

    def clear(self) -> None:
        self._events.clear()
        self._total_chars = 0

    def __len__(self) -> int:
        return len(self._events)

    @staticmethod
    def _event_chars(event: ContextEvent) -> str:
        if isinstance(event.content, str):
            return event.content
        return json.dumps(event.content)
