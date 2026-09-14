"""The ``multiedit`` tool: apply several edits to one file atomically."""

from typing import Annotated, NotRequired, TypedDict

from pydantic import Field
from pydantic_ai import ModelRetry, RunContext

from agent_harness._edit import read_for_edit, write_tracked
from agent_harness._fuzzy import EditError, EditOutcome, apply_edit
from agent_harness.deps import HarnessDeps

_FUZZY_NOTE = " (matched after normalizing per-line whitespace)"


class EditSpec(TypedDict):
    """A single edit within a :func:`multiedit` call."""

    old_string: Annotated[
        str,
        Field(
            description=(
                "The exact text to replace (must be unique in the file unless "
                "replace_all is set, and must differ from new_string)."
            )
        ),
    ]
    new_string: Annotated[str, Field(description="The replacement text.")]
    replace_all: NotRequired[
        Annotated[
            bool,
            Field(description="Replace every occurrence instead of requiring a unique match."),
        ]
    ]


def multiedit(ctx: RunContext[HarnessDeps], path: str, edits: list[EditSpec]) -> str:
    """Apply multiple edits to a single file in one atomic operation.

    Edits are applied in sequence, each to the result of the previous one; all
    succeed or none are applied. The file must have been read first, and read
    again if it changed on disk since. Each edit follows the same rules as
    ``edit_file``: exact text without line-number prefixes, and a unique match
    unless ``replace_all`` is set.

    Args:
        ctx: The tool run context (injected by the runtime).
        path: Path to edit, absolute or relative to the working directory.
        edits: The edits to apply, each with ``old_string``, ``new_string`` and
            an optional ``replace_all`` flag.

    Returns:
        A confirmation with a per-edit replacement summary.

    Raises:
        ModelRetry: If the file is missing or not freshly read, no edits are
            given, or any edit's ``old_string`` is not found or is ambiguous.
    """
    if not edits:
        raise ModelRetry("No edits provided: pass at least one old_string/new_string pair.")
    resolved, content = read_for_edit(ctx.deps, path)
    outcomes: list[EditOutcome] = []
    for index, edit in enumerate(edits, start=1):
        try:
            outcome = apply_edit(
                content,
                edit["old_string"],
                edit["new_string"],
                replace_all=edit.get("replace_all", False),
            )
        except EditError as exc:
            raise ModelRetry(f"Edit {index} of {len(edits)} failed: {exc}") from exc
        content = outcome.text
        outcomes.append(outcome)
    write_tracked(ctx.deps, resolved, content)
    summary = "\n".join(
        f"{index}. {outcome.count} replacement(s)" + (_FUZZY_NOTE if outcome.fuzzy else "")
        for index, outcome in enumerate(outcomes, start=1)
    )
    return f"The file {path} has been updated. Applied {len(edits)} edit(s):\n{summary}"
