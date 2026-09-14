"""Tests for the save_memory tool."""

from pydantic_ai import RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.save_memory import save_memory


def test_stores_and_reports(ctx: RunContext[HarnessDeps]) -> None:
    result = save_memory(ctx, "k", "v")
    assert ctx.deps.memory["k"] == "v"
    assert "k" in result


def test_overwrites_existing(ctx: RunContext[HarnessDeps]) -> None:
    _ = save_memory(ctx, "k", "v1")
    _ = save_memory(ctx, "k", "v2")
    assert ctx.deps.memory["k"] == "v2"
