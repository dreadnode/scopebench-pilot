"""Fuzzy text matching and single-edit application for the edit tools.

Matching tries an exact substring search first and falls back to a
whitespace-tolerant, line-based search (leading/trailing whitespace on each line
is ignored). This mirrors, in spirit, the multi-pass matching of the reference
``edit_file`` while staying simple enough to test exhaustively.
"""

import re
from dataclasses import dataclass, replace


class EditError(Exception):
    """Raised when an edit's ``old_string`` is invalid, missing, or ambiguous."""


@dataclass
class Match:
    """A matched span in the source text, as ``[start, end)`` char offsets."""

    start: int
    end: int


@dataclass(frozen=True)
class EditOutcome:
    """The result of one successful :func:`apply_edit`.

    Attributes:
        text: The edited content.
        count: The number of replacements made.
        fuzzy: Whether the line-trimmed fallback, not an exact match, found ``old``.
        changed_lines: 1-indexed inclusive line span of the first replacement in ``text``.
    """

    text: str
    count: int
    fuzzy: bool
    changed_lines: tuple[int, int]


# A line pasted from ``read`` output: a line number followed by a tab.
_READ_PREFIX_RE = re.compile(r"^\d+\t")
# Cap on match line numbers named in the ambiguity message.
_MAX_MATCH_LINES = 5


def apply_edit(content: str, old: str, new: str, *, replace_all: bool) -> EditOutcome:
    r"""Replace ``old`` with ``new`` in ``content``.

    CRLF content is normalized to ``\n`` for matching and splicing, then the result is
    restored to CRLF, so editing one line never rewrites the rest of the file's endings (the
    model's ``old``/``new`` use ``\n``, matching what ``read`` shows). LF content takes an
    unchanged fast path. This mirrors the dominant-ending policy of :func:`split_preserving`.
    The outcome's line numbers are unaffected by the normalization round-trip.

    Args:
        content: The text to edit.
        old: The text to find (exact, then line-trimmed fallback).
        new: The replacement text.
        replace_all: Replace every match rather than requiring a unique one.

    Returns:
        An :class:`EditOutcome` carrying the edited text and replacement details.

    Raises:
        EditError: If ``old`` is empty, equals ``new``, is not found, or matches
            more than once while ``replace_all`` is false.
    """
    if "\r\n" not in content:
        return _apply(content, old, new, replace_all=replace_all)
    outcome = _apply(
        content.replace("\r\n", "\n"),
        old.replace("\r\n", "\n"),
        new.replace("\r\n", "\n"),
        replace_all=replace_all,
    )
    return replace(outcome, text=outcome.text.replace("\n", "\r\n"))


def _apply(content: str, old: str, new: str, *, replace_all: bool) -> EditOutcome:
    if not old:
        raise EditError("old_string is empty; provide the exact text to replace")
    if old == new:
        raise EditError("No changes to make: old_string and new_string are exactly the same.")
    matches, trimmed = _find(content, old)
    if not matches:
        raise EditError(_not_found_message(old))
    if len(matches) > 1 and not replace_all:
        raise EditError(_ambiguous_message(content, old, matches))
    chosen = matches if replace_all else matches[:1]
    replacements = [
        _reindent(content[m.start : m.end], old, new) if trimmed else new for m in chosen
    ]
    result = content
    for match, replacement in zip(reversed(chosen), reversed(replacements), strict=True):
        result = result[: match.start] + replacement + result[match.end :]
    first = content[: chosen[0].start].count("\n") + 1
    return EditOutcome(
        text=result,
        count=len(chosen),
        fuzzy=trimmed,
        changed_lines=(first, first + replacements[0].count("\n")),
    )


def _not_found_message(old: str) -> str:
    """The not-found error, hinting when ``old`` looks pasted from ``read`` output."""
    lines = [line for line in old.splitlines() if line.strip()]
    hint = (
        "\nHint: old_string appears to include line-number prefixes from read output; remove them."
        if lines and all(_READ_PREFIX_RE.match(line) for line in lines)
        else ""
    )
    return f"String to replace not found in file.{hint}\nString: {old}"


def _ambiguous_message(content: str, old: str, matches: list[Match]) -> str:
    """The multiple-matches error, naming the (deduplicated, capped) match lines."""
    numbers = list(dict.fromkeys(content[: m.start].count("\n") + 1 for m in matches))
    shown = ", ".join(str(n) for n in numbers[:_MAX_MATCH_LINES])
    extra = f" (+{len(numbers) - _MAX_MATCH_LINES} more)" if len(numbers) > _MAX_MATCH_LINES else ""
    return (
        f"Found {len(matches)} matches of the string to replace, but replace_all is false. "
        "To replace all occurrences, set replace_all to true. To replace only one "
        "occurrence, please provide more context to uniquely identify the instance."
        f"\nMatches found at lines: {shown}{extra}."
        f"\nString: {old}"
    )


def _find(haystack: str, needle: str) -> tuple[list[Match], bool]:
    """Return ``(matches, trimmed)``; ``trimmed`` is True for the fuzzy fallback."""
    exact = _find_exact(haystack, needle)
    if exact:
        return exact, False
    return _find_line_trimmed(haystack, needle), True


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _reindent(matched: str, old: str, new: str) -> str:
    """Re-base ``new``'s indentation onto the matched source block's.

    A whitespace-tolerant match may land on a block indented differently from
    ``old``. Splicing ``new`` in verbatim would then silently reindent the file,
    so shift every non-blank line of ``new`` by the difference between the source
    block's leading whitespace and ``old``'s. Mixed tab/space indentation that
    cannot be reconciled is left untouched.
    """
    matched_lines = matched.splitlines()
    old_lines = old.splitlines()
    source_ws = _leading_ws(matched_lines[0]) if matched_lines else ""
    old_ws = _leading_ws(old_lines[0]) if old_lines else ""
    if source_ws == old_ws:
        return new
    if source_ws.startswith(old_ws):
        add, remove = source_ws[len(old_ws) :], ""
    elif old_ws.startswith(source_ws):
        add, remove = "", old_ws[len(source_ws) :]
    else:
        return new
    out: list[str] = []
    for line in new.split("\n"):
        if not line.strip():
            out.append(line)
        else:
            out.append(add + (line[len(remove) :] if remove and line.startswith(remove) else line))
    return "\n".join(out)


def _find_exact(haystack: str, needle: str) -> list[Match]:
    matches: list[Match] = []
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index == -1:
            break
        matches.append(Match(index, index + len(needle)))
        start = index + len(needle)
    return matches


def _find_line_trimmed(haystack: str, needle: str) -> list[Match]:
    needle_lines = [line.strip() for line in needle.strip().splitlines()]
    if not needle_lines:
        return []
    hay_lines = haystack.splitlines(keepends=True)
    stripped = [line.strip() for line in hay_lines]
    offsets: list[int] = []
    position = 0
    for line in hay_lines:
        offsets.append(position)
        position += len(line)
    matches: list[Match] = []
    span = len(needle_lines)
    for i in range(len(hay_lines) - span + 1):
        if stripped[i : i + span] == needle_lines:
            start = offsets[i]
            end = offsets[i + span - 1] + len(hay_lines[i + span - 1])
            matches.append(Match(start, end))
    return matches
