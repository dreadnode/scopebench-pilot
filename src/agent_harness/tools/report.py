"""The ``report`` tool: persist a named report artifact for the session."""

import re
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext

from agent_harness._fs import resolve_path
from agent_harness.deps import HarnessDeps, WebFormat

# The report formats are the same three the web tools use; reuse that alias.
ReportFormat = WebFormat
_EXT = {"markdown": "md", "text": "txt", "html": "html"}


def report(
    ctx: RunContext[HarnessDeps],
    content: str | None = None,
    *,
    source_path: str | None = None,
    title: str | None = None,
    filename: str | None = None,
    format: ReportFormat = "markdown",
) -> str:
    """Persist a named report artifact under the session reports directory.

    Exactly one of ``content`` or ``source_path`` must be provided.

    Args:
        ctx: The tool run context (injected by the runtime).
        content: The report body to persist.
        source_path: A file whose contents should be persisted instead.
        title: A human title, used to derive the filename when not given.
        filename: An explicit filename to use (a bare name, no path separators).
        format: One of ``"markdown"``, ``"text"``, or ``"html"``.

    Returns:
        A message with the absolute path of the saved report.

    Raises:
        ModelRetry: If neither or both of ``content``/``source_path`` are given,
            ``source_path`` does not exist, or ``filename`` is not a bare name.
    """
    if content is not None and source_path is not None:
        raise ModelRetry("Provide exactly one of content or source_path, not both.")
    if content is not None:
        body = content
    elif source_path is not None:
        src = resolve_path(ctx.deps.cwd, source_path)
        if not src.is_file():
            msg = f"source_path not found: {source_path}"
            raise ModelRetry(msg)
        body = src.read_text(encoding="utf-8")
    else:
        raise ModelRetry("Provide either content or source_path.")
    if filename is not None and (filename in {"", ".", ".."} or filename != Path(filename).name):
        raise ModelRetry("filename must be a bare name with no path separators.")
    ctx.deps.reports_dir.mkdir(parents=True, exist_ok=True)
    name = filename if filename is not None else _default_filename(title, format)
    dest = ctx.deps.reports_dir / name
    _ = dest.write_text(body, encoding="utf-8")
    return f"Saved {format} report to {dest}"


def _default_filename(title: str | None, fmt: ReportFormat) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "report").lower()).strip("-")
    return f"{slug or 'report'}.{_EXT[fmt]}"
