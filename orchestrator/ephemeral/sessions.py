import asyncio
import logging
import time
from dataclasses import dataclass, field

_log = logging.getLogger("ephemeral.sessions")

_MAX_ENTRIES = 500


@dataclass
class SessionData:
    session_id: str
    logs: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}
        self._lock = asyncio.Lock()

    def get_or_create(self, session_id: str) -> SessionData:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionData(session_id=session_id)
        return self._sessions[session_id]

    async def append_log(self, session_id: str, message: str) -> None:
        entry = {"ts": time.time(), "message": message}
        async with self._lock:
            session = self.get_or_create(session_id)
            session.logs.append(entry)
            if len(session.logs) > _MAX_ENTRIES:
                session.logs = session.logs[-_MAX_ENTRIES:]
        await self._broadcast(session_id, {"type": "log", "data": entry})

    async def append_tool_call(self, session_id: str, tool: str, args: dict, result: str) -> None:
        entry = {"ts": time.time(), "tool": tool, "args": args, "result": result}
        async with self._lock:
            session = self.get_or_create(session_id)
            session.tool_calls.append(entry)
            if len(session.tool_calls) > _MAX_ENTRIES:
                session.tool_calls = session.tool_calls[-_MAX_ENTRIES:]
        await self._broadcast(session_id, {"type": "tool_call", "data": entry})

    async def broadcast_all(self, event_type: str, data: dict) -> None:
        event = {"type": event_type, "ts": time.time(), "data": data}
        async with self._lock:
            all_subscribers = [q for s in self._sessions.values() for q in s.subscribers]
        for q in all_subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                _log.warning("SSE queue full, dropping event")

    async def broadcast(self, session_id: str, event: dict) -> None:
        await self._broadcast(session_id, event)

    async def _broadcast(self, session_id: str, event: dict) -> None:
        async with self._lock:
            if session_id not in self._sessions:
                return
            subscribers = list(self._sessions[session_id].subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                _log.warning("SSE queue full for session %s", session_id)

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self.get_or_create(session_id).subscribers.append(q)
        return q

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            if session_id in self._sessions:
                try:
                    self._sessions[session_id].subscribers.remove(queue)
                except ValueError:
                    pass

    def get_logs(self, session_id: str) -> list[dict]:
        if session_id not in self._sessions:
            return []
        return list(self._sessions[session_id].logs)

    def get_tool_calls(self, session_id: str) -> list[dict]:
        if session_id not in self._sessions:
            return []
        return list(self._sessions[session_id].tool_calls)

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())


_store = SessionStore()


def get_store() -> SessionStore:
    return _store
