"""The ``todo`` tool: manage a structured task list for the session."""

from collections import Counter

from pydantic_ai import RunContext

from agent_harness.deps import HarnessDeps, TodoItem

_STATUS_ORDER = ("pending", "in_progress", "completed", "cancelled")


def todo(ctx: RunContext[HarnessDeps], todos: list[TodoItem]) -> str:
    """Create and manage a structured task list for the current session.

    Pass the full, updated list on each call; it replaces the previous one.

    Args:
        ctx: The tool run context (injected by the runtime).
        todos: The complete, updated list of todo items.

    Returns:
        A summary line with per-status counts.
    """
    ctx.deps.todos = list(todos)
    counts = Counter(item["status"] for item in todos)
    parts = [f"{counts[status]} {status}" for status in _STATUS_ORDER if counts[status]]
    detail = f" ({', '.join(parts)})" if parts else ""
    return f"Updated todo list: {len(todos)} task(s){detail}"
