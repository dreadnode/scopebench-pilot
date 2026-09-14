"""The ``think`` tool: a no-op scratchpad for the model's reasoning."""


def think(thought: str) -> str:
    """Record a thought, reflection, or plan.

    This is a scratchpad for the model's own reasoning. It has no side effects
    beyond acknowledging the thought.

    Args:
        thought: The thought to record.

    Returns:
        A short acknowledgement.
    """
    return f"Recorded thought ({len(thought)} chars)."
