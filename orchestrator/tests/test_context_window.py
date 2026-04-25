import time
import pytest
from ephemeral.agents.provisioner.context import ContextEvent, ContextWindow


def _event(text: str, source: str = "agent") -> ContextEvent:
    return ContextEvent(ts=time.time(), source=source, content=text)


def test_add_and_len():
    w = ContextWindow(max_events=5, max_chars=1000)
    w.add(_event("hello"))
    assert len(w) == 1


def test_evicts_oldest_when_over_max_events():
    w = ContextWindow(max_events=3, max_chars=10000)
    for i in range(5):
        w.add(_event(f"event {i}"))
    assert len(w) == 3
    # oldest should be gone, only last 3 remain
    assert "event 4" in w.render_for_prompt()
    assert "event 0" not in w.render_for_prompt()


def test_evicts_oldest_when_over_max_chars():
    w = ContextWindow(max_events=100, max_chars=20)
    w.add(_event("a" * 15))
    w.add(_event("b" * 10))  # pushes total over 20 → first evicted
    assert len(w) == 1
    assert "b" in w.render_for_prompt()


def test_render_includes_source_tag():
    w = ContextWindow()
    w.add(_event("agent says hi", source="agent"))
    w.add(_event("system says hi", source="system"))
    rendered = w.render_for_prompt()
    assert "[agent]" in rendered
    assert "[system]" in rendered


def test_recent_text_is_lowercase():
    w = ContextWindow()
    w.add(_event("Pandas DataFrame CSV"))
    assert "pandas" in w.recent_text()
    assert "dataframe" in w.recent_text()
    assert "csv" in w.recent_text()


def test_clear_resets():
    w = ContextWindow()
    w.add(_event("something"))
    w.clear()
    assert len(w) == 0
    assert w.render_for_prompt() == ""


def test_dict_content_serialized():
    w = ContextWindow()
    w.add(ContextEvent(ts=time.time(), source="agent", content={"tool": "read_csv", "path": "data.csv"}))
    rendered = w.render_for_prompt()
    assert "read_csv" in rendered
