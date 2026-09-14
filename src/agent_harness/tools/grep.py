"""The ``grep`` tool: search file contents for a regex pattern."""

import os
import re
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal

from pydantic_ai import ModelRetry, RunContext

from agent_harness._fs import (
    BINARY_SUFFIXES,
    DEFAULT_IGNORES,
    MAX_LINE_CHARS,
    clip_output,
    resolve_path,
)
from agent_harness.deps import HarnessDeps

_MAX_CONTEXT = 20
_MAX_LIMIT = 1000
_NO_MATCHES = "No matches found"

GrepMode = Literal["content", "files", "count"]


def grep(
    ctx: RunContext[HarnessDeps],
    pattern: str,
    path: str | None = None,
    *,
    include: str | None = None,
    ignore_case: bool = False,
    literal: bool = False,
    context: int = 0,
    limit: int = 100,
    output_mode: GrepMode = "content",
) -> str:
    """Search file contents for a regex pattern.

    Results are grouped by file. Common directories (``.git``, ``node_modules``,
    ``__pycache__``, ``.venv``, ...) are always skipped.

    Args:
        ctx: The tool run context (injected by the runtime).
        pattern: The regular expression (or literal string, see ``literal``).
        path: File or directory to search; defaults to the working directory.
        include: Only search files whose name matches this glob (e.g. ``*.py``).
        ignore_case: Match case-insensitively.
        literal: Treat ``pattern`` as a literal string rather than a regex.
        context: Lines of context to show around each match (content mode only).
        limit: Maximum number of matches/files to report.
        output_mode: ``"content"``, ``"files"``, or ``"count"``.

    Returns:
        Matches grouped by file, a list of files, or per-file match counts.

    Raises:
        ModelRetry: If the pattern is not a valid regex, or the path is missing.
    """
    try:
        regex = re.compile(
            re.escape(pattern) if literal else pattern,
            re.IGNORECASE if ignore_case else 0,
        )
    except re.error as exc:
        msg = f"Invalid regex pattern {pattern!r}: {exc}"
        raise ModelRetry(msg) from exc
    context = max(0, min(context, _MAX_CONTEXT))
    limit = max(1, min(limit, _MAX_LIMIT))
    base = resolve_path(ctx.deps.cwd, path) if path is not None else ctx.deps.cwd
    if not base.exists():
        msg = f"Path not found: {base}"
        raise ModelRetry(msg)
    if output_mode == "files":
        return _grep_files(regex, base, include, limit)
    if output_mode == "count":
        return _grep_count(regex, base, include, limit)
    return _grep_content(regex, base, include, limit, context)


def _iter_files(base: Path, include: str | None) -> Iterator[Path]:
    if base.is_file():
        yield base
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [name for name in dirnames if name not in DEFAULT_IGNORES]
        for name in sorted(filenames):
            if name in DEFAULT_IGNORES:
                continue
            # Skip binaries in the walk (an explicit single-file target still greps them).
            if Path(name).suffix.lower() in BINARY_SUFFIXES:
                continue
            if include is not None and not fnmatch(name, include):
                continue
            yield Path(dirpath) / name


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _display(base: Path, path: Path) -> str:
    return path.name if base.is_file() else str(path.relative_to(base))


def _grep_files(regex: re.Pattern[str], base: Path, include: str | None, limit: int) -> str:
    matched: list[str] = []
    for path in _iter_files(base, include):
        if any(regex.search(line) for line in _read_lines(path)):
            matched.append(_display(base, path))
            if len(matched) >= limit:
                break
    return "\n".join(matched) if matched else _NO_MATCHES


def _grep_count(regex: re.Pattern[str], base: Path, include: str | None, limit: int) -> str:
    counts: list[str] = []
    for path in _iter_files(base, include):
        hits = sum(1 for line in _read_lines(path) if regex.search(line))
        if hits:
            counts.append(f"{_display(base, path)}: {hits}")
            if len(counts) >= limit:
                break
    return "\n".join(counts) if counts else _NO_MATCHES


def _grep_content(
    regex: re.Pattern[str],
    base: Path,
    include: str | None,
    limit: int,
    context: int,
) -> str:
    blocks: list[str] = []
    emitted = 0
    for path in _iter_files(base, include):
        lines = _read_lines(path)
        hits = [index for index, line in enumerate(lines) if regex.search(line)]
        if not hits:
            continue
        rendered: list[str] = []
        for index in hits:
            lo = max(0, index - context)
            hi = min(len(lines), index + context + 1)
            rendered.extend(f"{j + 1}: {lines[j][:MAX_LINE_CHARS]}" for j in range(lo, hi))
            emitted += 1
            if emitted >= limit:
                break
        blocks.append(f"{_display(base, path)}:\n" + "\n".join(rendered))
        if emitted >= limit:
            break
    return clip_output("\n\n".join(blocks)) if blocks else _NO_MATCHES
