"""The ``delete_lines`` tool: delete a range of lines from a file."""

from pydantic_ai import ModelRetry, RunContext

from agent_harness._edit import read_for_edit, write_tracked
from agent_harness._fs import join_preserving, split_preserving
from agent_harness.deps import HarnessDeps


def delete_lines(ctx: RunContext[HarnessDeps], path: str, start_line: int, end_line: int) -> str:
    """Delete a range of lines, 1-indexed and inclusive on both ends.

    The file must have been read first (and read again if it changed on disk
    since); its existing line endings are preserved.

    Args:
        ctx: The tool run context (injected by the runtime).
        path: Path to modify, absolute or relative to the working directory.
        start_line: First line to delete (1-indexed, inclusive).
        end_line: Last line to delete (1-indexed, inclusive).

    Returns:
        A summary of how many lines were deleted.

    Raises:
        ModelRetry: If the file is missing, has not been read at its current
            content, not valid UTF-8, or the range is invalid.
    """
    resolved, file_text = read_for_edit(ctx.deps, path)
    # Existing CRLF/LF endings survive via split/join_preserving + newline="" on write.
    lines, newline, trailing = split_preserving(file_text)
    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        msg = f"Invalid line range {start_line}-{end_line} for {path} ({len(lines)} lines)."
        raise ModelRetry(msg)
    del lines[start_line - 1 : end_line]
    text = join_preserving(lines, newline, trailing=trailing)
    write_tracked(ctx.deps, resolved, text)
    return f"Deleted {end_line - start_line + 1} line(s) from {path}."
