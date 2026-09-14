"""The ``ask_user`` tool: ask the user a question via PydanticAI's deferred flow.

The tool call is *deferred*: it raises :class:`CallDeferred`, so the run surfaces
a ``DeferredToolRequests`` and the caller supplies the answer as the tool result.
"""

from pydantic_ai import CallDeferred


def ask_user(question: str, options: list[str] | None = None) -> str:
    """Ask the user a question and wait for their response.

    This defers to the caller: the run pauses and the caller provides the answer
    via ``DeferredToolResults.calls``.

    Args:
        question: The question to ask.
        options: Optional suggested answers, surfaced to the caller as metadata.

    Returns:
        The user's response (supplied by the caller when resuming the run).
    """
    raise CallDeferred(metadata={"question": question, "options": options})
