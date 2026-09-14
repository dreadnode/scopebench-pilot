"""The ``read`` tool: read a file's contents or list a directory."""

import base64
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext

from agent_harness._fs import (
    BINARY_SUFFIXES,
    MAX_OUTPUT_CHARS,
    format_numbered_line,
    not_found_message,
    resolve_path,
)
from agent_harness.deps import FileRead, HarnessDeps, content_digest

_DEFAULT_LIMIT = 2000
# Cap the raw bytes we will base64-encode so a large binary can't flood the
# context; the encoded form is ~4/3 the raw size, kept near the text cap.
_MAX_BINARY_BYTES = MAX_OUTPUT_CHARS * 3 // 4

_CHANGED_NOTE = "[Note: this file has changed on disk since it was last read.]"
_EMPTY_WARNING = "Warning: the file exists but the contents are empty."


def read(
    ctx: RunContext[HarnessDeps],
    file_path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    """Read a file or list a directory.

    Text files are returned with each line prefixed by its 1-indexed line number
    and a tab. Up to 2000 lines are returned by default; page through longer
    files with ``offset``/``limit`` (truncated output names the offset to
    continue from). Lines longer than 2000 characters are clipped and marked
    ``[line truncated]``. Reading a file makes it editable by the editing tools;
    if it changes on disk afterwards it must be read again before further edits.
    Directories are returned as a listing (subdirectories carry a trailing
    ``/``); images and PDFs as base64-encoded data.

    Args:
        ctx: The tool run context (injected by the runtime).
        file_path: Path to read, absolute or relative to the working directory.
        offset: 1-indexed line to start reading from (text files only).
        limit: Maximum number of lines to return (text files only; values below
            1 mean the default 2000).

    Returns:
        The line-numbered file contents (or a warning for an empty file or an
        offset past the end), a directory listing, or base64 data.

    Raises:
        ModelRetry: If the path does not exist.
    """
    path = resolve_path(ctx.deps.cwd, file_path)
    if not path.exists():
        raise ModelRetry(not_found_message(path))
    if path.is_dir():
        return _render_directory(path)
    if path.suffix.lower() in BINARY_SUFFIXES:
        return _render_binary(path)
    data = path.read_bytes()
    body, recordable = _render_text(data.decode("utf-8", errors="replace"), offset, limit)
    # Freshness is per content digest: a read that showed current content refreshes
    # the record (and clears any pending staleness note); a past-EOF request showed
    # nothing, so it must not — editability requires having actually seen the bytes.
    digest = content_digest(data)
    record = ctx.deps.read_files.get(path)
    changed = record is not None and record.digest != digest
    if recordable:
        ctx.deps.read_files[path] = FileRead(digest=digest)
    return f"{_CHANGED_NOTE}\n{body}" if changed else body


def _render_directory(path: Path) -> str:
    entries = sorted(path.iterdir(), key=lambda p: p.name)
    return "\n".join(entry.name + ("/" if entry.is_dir() else "") for entry in entries)


def _render_binary(path: Path) -> str:
    kind = path.suffix.lstrip(".")
    size = path.stat().st_size
    if size > _MAX_BINARY_BYTES:
        return (
            f"[binary {kind} file, {size} bytes — too large to inline "
            f"(cap {_MAX_BINARY_BYTES} bytes); read it with a dedicated tool instead]"
        )
    data = path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"[base64-encoded {kind} data, {len(data)} bytes]\n{encoded}"


def _render_text(text: str, offset: int | None, limit: int | None) -> tuple[str, bool]:
    """Render line-numbered text; return ``(body, recordable)``.

    ``recordable`` is whether the read showed the model actual current content —
    at least one line, or the (empty) whole of an empty file — and so may refresh
    the file's freshness record. A past-EOF request shows nothing and must not.
    """
    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return _EMPTY_WARNING, True
    start = max(0, offset - 1) if offset else 0
    if start >= total:
        warning = (
            f"Warning: the file is shorter than the provided offset ({offset}); "
            + f"it has {total} lines."
        )
        return warning, False
    count = limit if limit is not None and limit >= 1 else _DEFAULT_LIMIT
    rendered: list[str] = []
    used = 0
    truncated = False
    for number, line in enumerate(lines[start : start + count], start=start + 1):
        entry = format_numbered_line(number, line)
        if used + len(entry) + 1 > MAX_OUTPUT_CHARS:
            truncated = True
            break
        rendered.append(entry)
        used += len(entry) + 1
    body = "\n".join(rendered)
    shown = len(rendered)
    if truncated:
        body += (
            f"\n... (output truncated: lines {start + 1}-{start + shown} of {total} "
            f"shown; continue with offset={start + shown + 1})"
        )
    return body, True
