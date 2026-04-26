"""Background telemetry client. Endpoint is fixed and not user-configurable."""
from __future__ import annotations

import atexit
import json
import os
import queue
import threading
import time
import uuid
from typing import Any, Dict, Optional

import requests

_INGEST_URL = os.environ.get("EPHEMERAL_INGEST_URL")

_BATCH_SIZE = 50
_FLUSH_INTERVAL_S = 2.0
_QUEUE_MAX = 10_000
_SHUTDOWN_SENTINEL = object()


class _Client:
    def __init__(self) -> None:
        self._q: "queue.Queue[Any]" = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread: Optional[threading.Thread] = None
        self._session = requests.Session()
        self._api_key: Optional[str] = None
        self._project: Optional[str] = None
        self._session_id = str(uuid.uuid4())
        self._started = False
        self._lock = threading.Lock()

    def init(self, api_key: Optional[str] = None, project: Optional[str] = None) -> None:
        with self._lock:
            self._api_key = api_key or os.environ.get("EPHEMERAL_API_KEY")
            self._project = project or os.environ.get("EPHEMERAL_PROJECT")
            if not self._started:
                self._thread = threading.Thread(target=self._run, name="ephemeral-telemetry", daemon=True)
                self._thread.start()
                self._started = True
                atexit.register(self.shutdown)

    def enqueue(self, event: Dict[str, Any]) -> None:
        if not self._started:
            self.init()
        event.setdefault("session_id", self._session_id)
        event.setdefault("ts", time.time())
        event.setdefault("event_id", str(uuid.uuid4()))
        if self._project:
            event.setdefault("project", self._project)
        try:
            self._q.put_nowait(event)
        except queue.Full:
            pass  # drop on overflow rather than block user code

    def flush(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while not self._q.empty() and time.time() < deadline:
            time.sleep(0.05)

    def shutdown(self) -> None:
        if not self._started:
            return
        try:
            self._q.put_nowait(_SHUTDOWN_SENTINEL)
        except queue.Full:
            return
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        batch: list = []
        last_flush = time.time()
        while True:
            timeout = max(0.0, _FLUSH_INTERVAL_S - (time.time() - last_flush))
            try:
                item = self._q.get(timeout=timeout)
            except queue.Empty:
                item = None

            if item is _SHUTDOWN_SENTINEL:
                if batch:
                    self._send(batch)
                return

            if item is not None:
                batch.append(item)

            if len(batch) >= _BATCH_SIZE or (batch and time.time() - last_flush >= _FLUSH_INTERVAL_S):
                self._send(batch)
                batch = []
                last_flush = time.time()

    def _send(self, batch: list) -> None:
        if not _INGEST_URL:
            return
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = json.dumps({"events": batch}).encode("utf-8")
        for attempt in range(3):
            try:
                resp = self._session.post(_INGEST_URL, data=payload, headers=headers, timeout=10)
                if resp.status_code < 500:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.5 * (2 ** attempt))


class Session:
    """An explicit agent session. Pass to @track so telemetry and container routing share the same ID.

    Usage:
        session = ephemeral.Session(api_key="eph_xxx")
        # Put session.id in your MCP config: EPHEMERAL_SESSION_ID=<session.id>

        @ephemeral.track(session=session)
        def run_agent(msg): ...
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self.id = session_id or str(uuid.uuid4())
        self._client = _Client()
        self._client._session_id = self.id
        self._client.init(api_key=api_key, project=project)

    def enqueue(self, event: Dict[str, Any]) -> None:
        self._client.enqueue(event)

    def flush(self, timeout: float = 5.0) -> None:
        self._client.flush(timeout=timeout)

    def shutdown(self) -> None:
        self._client.shutdown()

    def __repr__(self) -> str:
        return f"Session(id={self.id!r})"


_client = _Client()


def init(api_key: Optional[str] = None, project: Optional[str] = None) -> None:
    """Initialize the SDK. Optional — first event auto-inits from env vars."""
    _client.init(api_key=api_key, project=project)


def flush(timeout: float = 5.0) -> None:
    _client.flush(timeout=timeout)


def shutdown() -> None:
    _client.shutdown()


def _enqueue(event: Dict[str, Any], session: "Optional[Session]" = None) -> None:
    if session is not None:
        session.enqueue(event)
    else:
        _client.enqueue(event)
