"""Smoke tests for shelby.memory."""
from __future__ import annotations


def test_append_and_load_recent(tmp_memory):
    from shelby import memory
    memory.append_turn("hello", "evening Captain")
    memory.append_turn("what time", "seven thirty")
    out = memory.load_recent()
    assert len(out) == 2
    assert out[0]["user"] == "hello"
    assert out[1]["assistant"] == "seven thirty"


def test_empty_returns_no_preamble(tmp_memory):
    from shelby import memory
    assert memory.format_for_prompt() == ""


def test_format_for_prompt_renders_turns(tmp_memory):
    from shelby import memory
    memory.append_turn("set a timer for the oven", "timer set")
    snippet = memory.format_for_prompt()
    assert "PREVIOUS CONVERSATION CONTEXT" in snippet
    assert "set a timer for the oven" in snippet
    assert "timer set" in snippet


def test_clear_removes_file(tmp_memory):
    from shelby import memory
    memory.append_turn("a", "b")
    assert tmp_memory.exists()
    memory.clear()
    assert not tmp_memory.exists()
    assert memory.load_recent() == []


def test_trim_at_max_lines(tmp_memory, monkeypatch):
    from shelby import memory
    monkeypatch.setattr(memory, "MAX_LINES", 5)
    for i in range(20):
        memory.append_turn(f"q{i}", f"a{i}")
    out = memory.load_recent(n=memory.MAX_LINES)
    assert len(out) == 5
    # Oldest 15 should be gone, newest 5 retained.
    assert out[0]["user"] == "q15"
    assert out[-1]["user"] == "q19"


def test_blank_turn_is_skipped(tmp_memory):
    from shelby import memory
    memory.append_turn("", "")
    assert memory.load_recent() == []
