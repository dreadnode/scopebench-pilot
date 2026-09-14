"""The ``clear_memory`` tool: remove one or all keys from the session memory."""

from pydantic_ai import RunContext

from agent_harness.deps import HarnessDeps


def clear_memory(ctx: RunContext[HarnessDeps], key: str | None = None) -> str:
    """Clear a single key from the session memory, or all of it.

    Args:
        ctx: The tool run context (injected by the runtime).
        key: The key to clear. If omitted (or ``None``), all memory is cleared.

    Returns:
        A message describing what was cleared.
    """
    memory = ctx.deps.memory
    if key is None:
        count = len(memory)
        memory.clear()
        return f"Cleared all memory ({count} key(s))."
    if key in memory:
        del memory[key]
        return f"Cleared memory key: '{key}'"
    return f"No such memory key to clear: '{key}'"
