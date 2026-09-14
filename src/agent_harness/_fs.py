"""Shared filesystem helpers for the read/navigate/write tools."""

import difflib
from pathlib import Path

# Directory and file names skipped by ``ls`` and ``grep`` by default.
DEFAULT_IGNORES = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    },
)

# Output caps shared by the read/exec/search tools — one source of truth instead of each
# tool inventing its own limit and marker.
MAX_OUTPUT_CHARS = 50_000
MAX_LINE_CHARS = 2000

# Marker appended to a line clipped at MAX_LINE_CHARS. An unmarked clip reads as the
# whole line, and edit strings built from it can never match the file.
LINE_TRUNCATED_MARKER = "... [line truncated]"

# File extensions treated as binary: inlined as base64 by ``read``, skipped by ``grep``'s walk.
BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf"},
)


def resolve_path(cwd: Path, file_path: str) -> Path:
    """Resolve ``file_path`` against ``cwd`` (unless absolute) and canonicalize it.

    The result is run through ``Path.resolve`` so the read-before-edit gate keys files
    consistently: reading via one alias (``a/../b.txt``, a symlink) and editing via another
    map to the same path. ``resolve`` tolerates a not-yet-existing tail, so writing a new
    file still works.
    """
    path = Path(file_path)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def not_found_message(path: Path) -> str:
    """A ``File does not exist`` message, with ``difflib`` "Did you mean" suggestions."""
    parent = path.parent
    suggestions: list[str] = []
    if parent.is_dir():
        names = [p.name for p in parent.iterdir()]
        suggestions = difflib.get_close_matches(path.name, names, n=3)
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    return f"File does not exist: {path}.{hint}"


def format_numbered_line(number: int, line: str) -> str:
    """Render one ``read``-format output line: the line number, a tab, the text.

    Lines longer than :data:`MAX_LINE_CHARS` are clipped and marked with
    :data:`LINE_TRUNCATED_MARKER`. The editing tools' snippets use the same
    renderer, so the two formats never drift apart.
    """
    if len(line) > MAX_LINE_CHARS:
        return f"{number}\t{line[:MAX_LINE_CHARS]}{LINE_TRUNCATED_MARKER}"
    return f"{number}\t{line}"


def split_preserving(text: str) -> tuple[list[str], str, bool]:
    r"""Split ``text`` into lines while remembering how to rejoin it faithfully.

    Returns ``(lines, newline, trailing)`` where ``newline`` is the file's
    dominant line terminator (``\\r\\n`` if any CRLF is present, else ``\\n``) and
    ``trailing`` records whether the last line ends with a newline. An empty file
    is treated as trailing so that writing a first line yields a newline-terminated
    file, matching the POSIX convention.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text == "" or text.endswith(("\n", "\r"))
    return text.splitlines(), newline, trailing


def join_preserving(lines: list[str], newline: str, *, trailing: bool) -> str:
    """Rejoin ``lines`` with ``newline``, re-adding a trailing terminator if wanted.

    The inverse of :func:`split_preserving`. An empty ``lines`` yields an empty
    string (no lone terminator), so deleting every line leaves an empty file.
    """
    text = newline.join(lines)
    if lines and trailing:
        text += newline
    return text


def clip_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Bound ``text`` to roughly ``limit`` chars, keeping the head and tail.

    Exec and search output usually carries its most useful signal — a final error, exit line,
    or traceback — at the end, so both ends are kept and the elided middle is marked. Text
    already within ``limit`` is returned unchanged.
    """
    if len(text) <= limit:
        return text
    keep = limit // 2
    omitted = len(text) - 2 * keep
    return f"{text[:keep]}\n... ({omitted} chars truncated) ...\n{text[-keep:]}"
