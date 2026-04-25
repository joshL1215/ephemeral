"""User-facing decorator and helpers for tracking LLM conversations."""
from __future__ import annotations

import functools
import time
import traceback
import uuid
from typing import Any, Callable, Dict, Iterable, Iterator, Optional

from .client import _enqueue, Session


def _normalize(value: Any) -> Any:
    """Best-effort JSON-safe normalization of LLM payloads."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    for attr in ("model_dump", "dict", "to_dict"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return _normalize(fn())
            except Exception:
                pass
    return repr(value)


def log(
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    *,
    name: Optional[str] = None,
    model: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Manually log a conversation snapshot."""
    _enqueue({
        "type": "conversation",
        "name": name,
        "model": model,
        "messages": _normalize(list(messages)) if messages is not None else None,
        "metadata": _normalize(metadata) if metadata else None,
    })


def track(
    _fn: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    model: Optional[str] = None,
    capture_args: bool = True,
    session: Optional[Session] = None,
) -> Callable:
    """Wrap a function that produces or consumes LLM context. Streams an event per call.

    The wrapped function may return:
      - a list of message dicts (conversation log)
      - a string (treated as assistant output)
      - a generator/iterator (streamed and concatenated)
      - any other value (captured as `output`)
    """

    def decorator(fn: Callable) -> Callable:
        trace_name = name or getattr(fn, "__qualname__", getattr(fn, "__name__", "anonymous"))

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            trace_id = str(uuid.uuid4())
            start = time.time()
            event: Dict[str, Any] = {
                "type": "trace",
                "trace_id": trace_id,
                "name": trace_name,
                "model": model,
            }
            if session is not None:
                event["session_id"] = session.id
            if capture_args:
                event["inputs"] = _normalize({"args": args, "kwargs": kwargs})

            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                event["error"] = {"type": type(e).__name__, "message": str(e), "traceback": traceback.format_exc()}
                event["duration_ms"] = (time.time() - start) * 1000
                _enqueue(event, session=session)
                raise

            if hasattr(result, "__iter__") and not isinstance(result, (str, bytes, list, dict, tuple)):
                return _wrap_stream(result, event, start, session=session)

            event.update(_summarize_output(result))
            event["duration_ms"] = (time.time() - start) * 1000
            _enqueue(event, session=session)
            return result

        return wrapper

    return decorator(_fn) if callable(_fn) else decorator


def _summarize_output(result: Any) -> Dict[str, Any]:
    if isinstance(result, list) and result and isinstance(result[0], dict) and "role" in result[0]:
        return {"messages": _normalize(result)}
    if isinstance(result, str):
        return {"output": result}
    return {"output": _normalize(result)}


def _wrap_stream(it: Iterator, event: Dict[str, Any], start: float, session: Optional[Session] = None) -> Iterator:
    chunks: list = []

    def gen():
        try:
            for chunk in it:
                chunks.append(chunk)
                yield chunk
        except Exception as e:
            event["error"] = {"type": type(e).__name__, "message": str(e)}
            event["duration_ms"] = (time.time() - start) * 1000
            event["output"] = _join_chunks(chunks)
            _enqueue(event, session=session)
            raise
        event["duration_ms"] = (time.time() - start) * 1000
        event["output"] = _join_chunks(chunks)
        _enqueue(event, session=session)

    return gen()


def _join_chunks(chunks: list) -> Any:
    if all(isinstance(c, str) for c in chunks):
        return "".join(chunks)
    return _normalize(chunks)
