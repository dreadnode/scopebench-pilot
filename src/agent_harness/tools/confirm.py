"""The ``confirm`` tool: ask the user to approve an action.

Registered with ``requires_approval=True``, so the run surfaces the call in
``DeferredToolRequests.approvals``. The body runs only once the caller approves;
denial is reported to the model by the framework as a ``ToolDenied`` message.
"""


def confirm(action: str) -> str:
    """Ask the user to confirm an action before proceeding.

    Args:
        action: A description of the action to confirm.

    Returns:
        A confirmation message (only reached when the caller approves).
    """
    return f"Approved: {action}"
