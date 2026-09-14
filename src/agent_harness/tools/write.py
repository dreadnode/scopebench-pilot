"""The ``write`` tool: write complete file contents."""

from pydantic_ai import RunContext

from agent_harness._edit import check_current_read, write_tracked
from agent_harness._fs import resolve_path
from agent_harness.deps import HarnessDeps


def write(ctx: RunContext[HarnessDeps], file_path: str, content: str) -> str:
    """Write complete content to a file, creating parent directories as needed.

    Creating a new file needs no prior read. Overwriting an existing file
    destroys its previous contents, so the file must have been read first, at
    its current on-disk content — read it again if it changed since. Prefer
    ``edit_file`` or ``multiedit`` for modifying an existing file; use ``write``
    for new files or full rewrites. After a write the file counts as read, so
    it may be edited immediately.

    Args:
        ctx: The tool run context (injected by the runtime).
        file_path: Path to write, absolute or relative to the working directory.
        content: The full contents to write.

    Returns:
        A confirmation naming the created or updated file.

    Raises:
        ModelRetry: If the target exists but has not been read at its current
            content.
    """
    resolved = resolve_path(ctx.deps.cwd, file_path)
    existed = resolved.exists()
    if resolved.is_file():
        check_current_read(ctx.deps, file_path, resolved, resolved.read_bytes(), "overwriting")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    write_tracked(ctx.deps, resolved, content)
    if existed:
        return f"File updated successfully at: {file_path}"
    return f"File created successfully at: {file_path}"
