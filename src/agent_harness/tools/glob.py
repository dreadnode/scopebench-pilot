"""The ``glob`` tool: find files matching a glob pattern."""

from pydantic_ai import ModelRetry, RunContext

from agent_harness._fs import resolve_path
from agent_harness.deps import HarnessDeps

_MAX_RESULTS = 100


def glob(
    ctx: RunContext[HarnessDeps],
    pattern: str,
    path: str | None = None,
) -> str:
    """Find files matching a glob pattern, sorted by modification time.

    Args:
        ctx: The tool run context (injected by the runtime).
        pattern: A glob pattern such as ``**/*.py`` or ``src/**/*.ts``.
        path: Directory to search from, absolute or relative to the working
            directory; defaults to the working directory.

    Returns:
        Newline-separated matching paths (newest first, capped at 100), or the
        string ``"No files found"``.

    Raises:
        ModelRetry: If the pattern is not a valid relative glob.
    """
    base = resolve_path(ctx.deps.cwd, path) if path is not None else ctx.deps.cwd
    try:
        matches = [candidate for candidate in base.glob(pattern) if candidate.is_file()]
    except (ValueError, NotImplementedError) as exc:
        msg = f"Invalid glob pattern {pattern!r}: {exc}"
        raise ModelRetry(msg) from exc
    if not matches:
        return "No files found"
    matches.sort(key=lambda candidate: candidate.stat().st_mtime, reverse=True)
    limited = matches[:_MAX_RESULTS]
    body = "\n".join(str(candidate.relative_to(base)) for candidate in limited)
    if len(matches) > _MAX_RESULTS:
        body += f"\n... ({len(matches) - _MAX_RESULTS} more)"
    return body
