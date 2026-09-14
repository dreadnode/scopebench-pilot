"""Tests for the clear_memory tool."""

from pydantic_ai import RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.clear_memory import clear_memory


def test_clear_all(ctx: RunContext[HarnessDeps]) -> None:
    ctx.deps.memory.update({"a": "1", "b": "2"})
    msg = clear_memory(ctx)
    assert ctx.deps.memory == {}
    assert "2" in msg


def test_clear_existing_key(ctx: RunContext[HarnessDeps]) -> None:
    ctx.deps.memory.update({"a": "1", "b": "2"})
    msg = clear_memory(ctx, "a")
    assert ctx.deps.memory == {"b": "2"}
    assert "a" in msg


def test_clear_missing_key(ctx: RunContext[HarnessDeps]) -> None:
    msg = clear_memory(ctx, "nope")
    assert "No such memory key" in msg
