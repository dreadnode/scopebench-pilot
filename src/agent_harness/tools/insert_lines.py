"""The ``insert_lines`` tool: insert content at a specific line number."""

from pydantic_ai import RunContext

from agent_harness._edit import read_for_edit, write_tracked
from agent_harness._fs import join_preserving, split_preserving
from agent_harness.deps import HarnessDeps


def insert_lines(ctx: RunContext[HarnessDeps], path: str, line_number: int, content: str) -> str:
    """Insert content before a given 1-indexed line.

    Use ``1`` to insert at the start; a line number past the end appends. The
    file must have been read first (and read again if it changed on disk
    since); its existing line endings are preserved.

    Args:
        ctx: The tool run context (injected by the runtime).
        path: Path to modify, absolute or relative to the working directory.
        line_number: 1-indexed line to insert before.
        content: The content to insert (may span multiple lines).

    Returns:
        A summary of how many lines were inserted.

    Raises:
        ModelRetry: If the file is missing, has not been read at its current
            content, or is not valid UTF-8.
    """
    resolved, file_text = read_for_edit(ctx.deps, path)
    # Existing CRLF/LF endings survive via split/join_preserving + newline="" on write.
    lines, newline, trailing = split_preserving(file_text)
    insert = content.splitlines()
    index = min(max(line_number - 1, 0), len(lines))
    new_lines = lines[:index] + insert + lines[index:]
    text = join_preserving(new_lines, newline, trailing=trailing)
    write_tracked(ctx.deps, resolved, text)
    return f"Inserted {len(insert)} line(s) at line {line_number} in {path}."
