"""The ``ls`` tool: list files and directories in a tree-style view."""

import os
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext

from agent_harness._fs import DEFAULT_IGNORES, resolve_path
from agent_harness.deps import HarnessDeps

_MAX_ENTRIES = 100


def ls(
    ctx: RunContext[HarnessDeps],
    path: str | None = None,
    ignore: list[str] | None = None,
) -> str:
    """List files and directories in a tree-style view.

    Common directories (``.git``, ``node_modules``, ``__pycache__``, ``.venv``,
    ``dist``, ``build``, ...) are ignored by default.

    Args:
        ctx: The tool run context (injected by the runtime).
        path: Directory to list, absolute or relative to the working directory;
            defaults to the working directory.
        ignore: Additional file/directory names to skip.

    Returns:
        A 2-space-indented tree, capped at 100 entries.

    Raises:
        ModelRetry: If the path is not an existing directory.
    """
    root = resolve_path(ctx.deps.cwd, path) if path is not None else ctx.deps.cwd
    if not root.is_dir():
        raise ModelRetry(f"Not a directory: {root}")
    ignores = DEFAULT_IGNORES | set(ignore or [])
    lines: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in ignores)
        current = Path(dirpath)
        depth = len(current.relative_to(root).parts)
        if current != root:
            lines.append("  " * (depth - 1) + current.name + "/")
        for name in sorted(filenames):
            if name in ignores:
                continue
            lines.append("  " * depth + name)
            if len(lines) >= _MAX_ENTRIES:
                truncated = True
                break
        if truncated:
            break
    body = "\n".join(lines)
    if truncated:
        body += f"\n... (truncated at {_MAX_ENTRIES} entries)"
    return body
