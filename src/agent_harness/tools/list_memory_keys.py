"""The ``list_memory_keys`` tool: enumerate keys in the session memory."""

from pydantic_ai import RunContext

from agent_harness.deps import HarnessDeps


def list_memory_keys(ctx: RunContext[HarnessDeps]) -> list[str]:
    """List all keys currently present in the session memory.

    Args:
        ctx: The tool run context (injected by the runtime).

    Returns:
        The stored keys, sorted alphabetically.
    """
    return sorted(ctx.deps.memory)
