"""Tests for the retrieve_memory tool."""

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.retrieve_memory import retrieve_memory


def test_returns_stored_value(ctx: RunContext[HarnessDeps]) -> None:
    ctx.deps.memory["k"] = "v"
    assert retrieve_memory(ctx, "k") == "v"


def test_missing_key_lists_available(ctx: RunContext[HarnessDeps]) -> None:
    ctx.deps.memory["a"] = "1"
    with pytest.raises(ModelRetry) as exc:
        _ = retrieve_memory(ctx, "missing")
    message = str(exc.value)
    assert "missing" in message
    assert "a" in message


def test_missing_key_empty_memory(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry) as exc:
        _ = retrieve_memory(ctx, "x")
    assert "(none)" in str(exc.value)
