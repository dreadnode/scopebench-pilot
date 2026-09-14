"""Tests for the todo tool."""

from pydantic_ai import RunContext

from agent_harness.deps import HarnessDeps, TodoItem
from agent_harness.tools.todo import todo


def test_replaces_and_summarizes(ctx: RunContext[HarnessDeps]) -> None:
    todos: list[TodoItem] = [
        {"id": "1", "content": "a", "status": "pending", "priority": "high"},
        {"id": "2", "content": "b", "status": "completed", "priority": "low"},
    ]
    msg = todo(ctx, todos)
    assert ctx.deps.todos == todos
    assert "2 task(s)" in msg
    assert "1 pending" in msg
    assert "1 completed" in msg
    assert "in_progress" not in msg


def test_empty(ctx: RunContext[HarnessDeps]) -> None:
    assert todo(ctx, []) == "Updated todo list: 0 task(s)"
