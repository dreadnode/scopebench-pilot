"""The ``edit_file`` tool: surgical text replacement in a file."""

from pydantic_ai import ModelRetry, RunContext

from agent_harness._edit import read_for_edit, render_snippet, write_tracked
from agent_harness._fuzzy import EditError, apply_edit
from agent_harness.deps import HarnessDeps


def edit_file(
    ctx: RunContext[HarnessDeps],
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Replace ``old_string`` with ``new_string`` in a file.

    The file must have been read first, and read again if it changed on disk
    since. Provide the exact text to replace: never include the line-number
    prefixes that ``read`` output shows. Matching is exact where possible, with
    a fallback that ignores per-line leading/trailing whitespace. The edit fails
    if ``old_string`` is not unique in the file; either include enough
    surrounding context to identify one occurrence uniquely, or set
    ``replace_all`` to change every occurrence (useful for renames).

    Args:
        ctx: The tool run context (injected by the runtime).
        path: Path to edit, absolute or relative to the working directory.
        old_string: The exact text to replace (must differ from ``new_string``).
        new_string: The replacement text.
        replace_all: Replace every occurrence instead of requiring a unique match.

    Returns:
        A confirmation with a line-numbered snippet of the edited region (with
        ``replace_all``, a confirmation without a snippet).

    Raises:
        ModelRetry: If the file is missing or not freshly read, ``old_string`` is
            not found or is ambiguous, or the edit would change nothing.
    """
    resolved, content = read_for_edit(ctx.deps, path)
    try:
        outcome = apply_edit(content, old_string, new_string, replace_all=replace_all)
    except EditError as exc:
        raise ModelRetry(str(exc)) from exc
    write_tracked(ctx.deps, resolved, outcome.text)
    note = " (old_string matched after normalizing per-line whitespace)" if outcome.fuzzy else ""
    if replace_all:
        return (
            f"The file {path} has been updated{note}. All occurrences were successfully replaced."
        )
    snippet = render_snippet(outcome.text, *outcome.changed_lines)
    return (
        f"The file {path} has been updated{note}. "
        "Here's the result of running `cat -n` on a snippet of the edited file:\n"
        f"{snippet}"
    )
