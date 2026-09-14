"""Parser and text applier for the ``apply_patch`` tool's diff envelope.

The format is the file-oriented patch envelope::

    *** Begin Patch
    *** Add File: path/new.txt
    +new content
    *** Update File: path/existing.txt
    *** Move to: path/renamed.txt
    @@ optional anchor
     context line
    -removed line
    +added line
    *** Delete File: path/gone.txt
    *** End Patch
"""

from dataclasses import dataclass, field
from typing import Literal

from agent_harness._fs import join_preserving, split_preserving

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File: "
_UPDATE = "*** Update File: "
_DELETE = "*** Delete File: "
_MOVE = "*** Move to: "
_HUNK = "@@"

FileAction = Literal["add", "update", "delete"]
HunkLine = tuple[str, str]


class PatchError(Exception):
    """Raised when a patch is malformed or cannot be applied."""


@dataclass
class Hunk:
    """One ``@@`` hunk: an optional anchor plus its context/edit lines.

    ``anchor`` is the text after the ``@@`` marker (``None`` when absent). It
    disambiguates *where* the hunk applies when its context block occurs more
    than once: the search for the context starts at the first line containing
    the anchor.
    """

    anchor: str | None = None
    lines: list[HunkLine] = field(default_factory=list)


@dataclass
class FileOp:
    """A single file operation parsed from a patch."""

    action: FileAction
    path: str
    move_to: str | None = None
    add_lines: list[str] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)


def parse_patch(text: str) -> list[FileOp]:
    """Parse a patch envelope into a list of file operations.

    Raises:
        PatchError: If the envelope or any line is malformed.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _BEGIN:
        msg = f"Patch must start with {_BEGIN!r}"
        raise PatchError(msg)
    if lines[-1].strip() != _END:
        msg = f"Patch must end with {_END!r}"
        raise PatchError(msg)
    ops: list[FileOp] = []
    current: FileOp | None = None
    for line in lines[1:-1]:
        if line.startswith(_ADD):
            current = FileOp(action="add", path=line.removeprefix(_ADD).strip())
            ops.append(current)
        elif line.startswith(_UPDATE):
            current = FileOp(action="update", path=line.removeprefix(_UPDATE).strip())
            ops.append(current)
        elif line.startswith(_DELETE):
            ops.append(FileOp(action="delete", path=line.removeprefix(_DELETE).strip()))
            current = None
        elif line.startswith(_MOVE):
            if current is None or current.action != "update":
                msg = f"{_MOVE!r} must follow an update section"
                raise PatchError(msg)
            current.move_to = line.removeprefix(_MOVE).strip()
        elif line.startswith(_HUNK):
            if current is None or current.action != "update":
                msg = "Hunk marker '@@' outside an update section"
                raise PatchError(msg)
            current.hunks.append(Hunk(anchor=line.removeprefix(_HUNK).strip() or None))
        else:
            _parse_body_line(current, line)
    return ops


def _parse_body_line(current: FileOp | None, line: str) -> None:
    if current is None:
        msg = f"Unexpected line outside a file section: {line!r}"
        raise PatchError(msg)
    if current.action == "add":
        if not line.startswith("+"):
            msg = f"Add File lines must start with '+': {line!r}"
            raise PatchError(msg)
        current.add_lines.append(line.removeprefix("+"))
    else:
        if not line or line[0] not in (" ", "+", "-"):
            msg = f"Invalid hunk line: {line!r}"
            raise PatchError(msg)
        if not current.hunks:
            current.hunks.append(Hunk())
        current.hunks[-1].lines.append((line[0], line[1:]))


def apply_hunks(content: str, hunks: list[Hunk]) -> str:
    """Apply update ``hunks`` to ``content`` and return the new text.

    Existing line endings and trailing-newline state are preserved. When a hunk
    carries an anchor, the search for its context begins at the anchor line so a
    context block that repeats in the file is applied at the intended site.

    Raises:
        PatchError: If a hunk's context cannot be located.
    """
    lines, newline, trailing = split_preserving(content)
    for hunk in hunks:
        old = [text for kind, text in hunk.lines if kind in (" ", "-")]
        new = [text for kind, text in hunk.lines if kind in (" ", "+")]
        index = _find_block(lines, old, _anchor_index(lines, hunk.anchor))
        if index is None:
            msg = f"Could not locate hunk context: {old!r}"
            raise PatchError(msg)
        lines[index : index + len(old)] = new
    return join_preserving(lines, newline, trailing=trailing)


def _anchor_index(lines: list[str], anchor: str | None) -> int:
    """First line index containing ``anchor`` (0 if no anchor or none matches)."""
    if not anchor:
        return 0
    for i, line in enumerate(lines):
        if anchor in line:
            return i
    return 0


def _find_block(lines: list[str], block: list[str], start: int = 0) -> int | None:
    if not block:
        return start
    for i in range(start, len(lines) - len(block) + 1):
        if lines[i : i + len(block)] == block:
            return i
    return None
