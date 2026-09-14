"""Tests for the list_memory_keys tool."""

from pydantic_ai import RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.list_memory_keys import list_memory_keys


def test_empty_memory(ctx: RunContext[HarnessDeps]) -> None:
    assert list_memory_keys(ctx) == []


def test_returns_sorted_keys(ctx: RunContext[HarnessDeps]) -> None:
    ctx.deps.memory.update({"b": "1", "a": "2"})
    assert list_memory_keys(ctx) == ["a", "b"]
