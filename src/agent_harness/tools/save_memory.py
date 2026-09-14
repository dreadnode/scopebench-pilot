"""The ``save_memory`` tool: store a value in the session key-value memory."""

from pydantic_ai import RunContext

from agent_harness.deps import HarnessDeps


def save_memory(ctx: RunContext[HarnessDeps], key: str, value: str) -> str:
    """Store a value in the session memory, overwriting any existing value.

    Args:
        ctx: The tool run context (injected by the runtime).
        key: The key to store the value under.
        value: The value to store.

    Returns:
        A confirmation message.
    """
    ctx.deps.memory[key] = value
    return f"Value saved to memory key: '{key}'"
