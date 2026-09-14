"""The ``retrieve_memory`` tool: read a value from the session key-value memory."""

from pydantic_ai import ModelRetry, RunContext

from agent_harness.deps import HarnessDeps


def retrieve_memory(ctx: RunContext[HarnessDeps], key: str) -> str:
    """Retrieve a value previously stored in the session memory.

    Args:
        ctx: The tool run context (injected by the runtime).
        key: The key to retrieve.

    Returns:
        The stored value.

    Raises:
        ModelRetry: If no value is stored under ``key``.
    """
    memory = ctx.deps.memory
    if key not in memory:
        available = ", ".join(sorted(memory)) or "(none)"
        msg = f"No value stored under memory key {key!r}. Available keys: {available}"
        raise ModelRetry(msg)
    return memory[key]
