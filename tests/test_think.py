"""Tests for the think tool."""

from agent_harness.tools.think import think


def test_think_acknowledges_length() -> None:
    out = think("consider the plan")
    assert "chars" in out
    assert str(len("consider the plan")) in out
