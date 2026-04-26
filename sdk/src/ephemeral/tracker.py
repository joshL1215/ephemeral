"""User-facing decorator and helpers for tracking LLM conversations."""
from __future__ import annotations

import functools
import time
import traceback
import uuid
from typing import Any, Callable, Dict, Iterable, Iterator, Optional

from .client import _enqueue, _send_context, Session


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
    _arg: Any = None,
    *,
    name: Optional[str] = None,
    model: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    session: Optional[Session] = None,
) -> Any:
    """Log a conversation snapshot, OR wrap a function to auto-stream
    (user_input, ai_output) pairs to the static context endpoint.

    Three forms:
        # 1. Manual snapshot
        ephemeral.log(messages, name="...", model="...")

        # 2. Bare decorator
        @ephemeral.log
        def call_llm(messages): ...

        # 3. Decorator with options
        @ephemeral.log(name="agent")
        def run_agent(task): ...

    When used as a decorator, every call extracts the latest user-role message
    from the inputs and the assistant text from the return value. If both are
    present, a single combined event is POSTed to the provisioner so it can
    pre-warm the right container before the next turn.
    """
    if callable(_arg):
        return _wrap_for_pair_streaming(_arg, session=session)

    if _arg is None:
        def decorator(fn: Callable) -> Callable:
            return _wrap_for_pair_streaming(fn, session=session)
        return decorator

    _enqueue({
        "type": "conversation",
        "name": name,
        "model": model,
        "messages": _normalize(list(_arg)),
        "metadata": _normalize(metadata) if metadata else None,
    }, session=session)
    return None


def _content_to_text(content: Any) -> str:
    """Flatten Anthropic-style content (str | list[block]) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = None
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text = block.get("text")
                elif block.get("type") == "tool_use":
                    text = f"[tool_use {block.get('name')}({block.get('input')})]"
                elif block.get("type") == "tool_result":
                    inner = block.get("content")
                    text = inner if isinstance(inner, str) else _content_to_text(inner)
            else:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text = getattr(block, "text", None)
                elif btype == "tool_use":
                    text = f"[tool_use {getattr(block, 'name', '?')}({getattr(block, 'input', None)})]"
                elif btype == "tool_result":
                    text = _content_to_text(getattr(block, "content", None))
            if text:
                parts.append(text)
        return "\n".join(parts)
    return str(content)


def _find_messages_arg(args: tuple, kwargs: dict) -> Optional[list]:
    """Find a list-of-message-dicts in the call arguments."""
    cand = kwargs.get("messages")
    if isinstance(cand, list):
        return cand
    for a in args:
        if isinstance(a, list) and a and isinstance(a[0], dict) and "role" in a[0]:
            return a
    return None


def _last_user_text(messages: list) -> Optional[str]:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = _content_to_text(msg.get("content"))
            return text or None
    return None


def _assistant_text_from_result(result: Any) -> Optional[str]:
    """Extract assistant text from an LLM response or messages list."""
    content = getattr(result, "content", None)
    if content is not None and not isinstance(result, dict):
        text = _content_to_text(content)
        if text:
            return text
    if isinstance(result, list) and result and isinstance(result[0], dict):
        for msg in reversed(result):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                text = _content_to_text(msg.get("content"))
                if text:
                    return text
    if isinstance(result, str):
        return result or None
    return None


def _wrap_for_pair_streaming(fn: Callable, session: Optional[Session] = None) -> Callable:
    """Decorator: on each call, extract (user, assistant) pair and stream it."""
    sid = session.id if session is not None else None

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        user_text = None
        msgs_in = _find_messages_arg(args, kwargs)
        if msgs_in is not None:
            user_text = _last_user_text(msgs_in)
        else:
            for a in args:
                if isinstance(a, str):
                    user_text = a
                    break

        result = fn(*args, **kwargs)

        ai_text = _assistant_text_from_result(result)

        if isinstance(result, list) and result and isinstance(result[0], dict) \
                and "role" in result[0] and msgs_in is None:
            # The function returned a full conversation — stream every pair.
            _stream_all_pairs(result, sid)
        elif user_text and ai_text:
            _send_context(f"user: {user_text}\nassistant: {ai_text}", session_id=sid)

        return result

    return wrapper


def _stream_all_pairs(messages: list, session_id: Optional[str]) -> None:
    """Walk a messages list and send each user→assistant turn as one context event."""
    pending_user: Optional[str] = None
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        text = _content_to_text(msg.get("content"))
        if role == "user" and text:
            pending_user = text
        elif role == "assistant" and text and pending_user:
            _send_context(f"user: {pending_user}\nassistant: {text}", session_id=session_id)
            pending_user = None


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
