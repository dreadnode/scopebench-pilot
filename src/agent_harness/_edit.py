"""The read-before-edit gate shared by the file-modifying tools.

Every mutation of an existing file (``edit_file``, ``multiedit``, ``insert_lines``,
``delete_lines``, ``apply_patch`` updates, and ``write`` overwrites) must pass
:func:`check_current_read`: the file has been read this run, and that read (or the
model's own last write) saw the exact bytes now on disk, compared by content digest.
The digest check catches files changed by ``bash``/``python`` or anything external,
so an edit can never be applied against a stale mental snapshot.

Tools that write a file record it as read via :func:`write_tracked` — the model
authored those bytes — which is what keeps a file editable across its own successive
edits. :func:`staleness_notes` is the proactive half of the same bookkeeping: after a
shell or Python command, it names tracked files whose bytes changed underneath the
model (once per new content), so the gate's refusal is never the first warning.

Centralising this here keeps the tools byte-for-byte consistent and gives one place to
decide the non-UTF-8 policy: such a file is *refused* with a retryable :class:`ModelRetry`
rather than silently repaired (which would persist U+FFFD and corrupt bytes the model never
saw).
"""

from pathlib import Path

from pydantic_ai import ModelRetry

from agent_harness._fs import format_numbered_line, not_found_message, resolve_path
from agent_harness.deps import FileRead, HarnessDeps, content_digest

# Lines of context shown on each side of an edited region in a snippet.
_SNIPPET_CONTEXT = 4
# Cap on files named per staleness scan; the rest are counted and named on a later scan.
_MAX_STALE_NOTES = 5
# ``FileRead.noted`` sentinel for an announced deletion; cannot collide with a hex digest.
_NOTED_DELETED = "deleted"


def check_current_read(
    deps: HarnessDeps,
    path: str,
    resolved: Path,
    data: bytes,
    action: str = "editing",
) -> None:
    """Require that ``path`` has been read at the exact bytes now on disk.

    Args:
        deps: The per-run state holding the read records.
        path: The path as the model spelled it, for messages.
        resolved: The canonical path keying the read record.
        data: The file's current raw bytes, digested against the record.
        action: Verb for the refusal messages (``"editing"``/``"overwriting"``).

    Raises:
        ModelRetry: If the file was never read or has changed on disk since it
            was read.
    """
    record = deps.read_files.get(resolved)
    if record is None:
        raise ModelRetry(f"{path} has not been read yet. Read it first before {action} it.")
    if record.digest != content_digest(data):
        msg = f"{path} has been modified since it was read. Read it again before {action} it."
        raise ModelRetry(msg)


def require_read(deps: HarnessDeps, path: str, action: str = "editing") -> tuple[Path, bytes]:
    """Resolve ``path``, require it to exist, and enforce the read gate on its bytes.

    Returns:
        A ``(resolved, data)`` tuple: the canonical path and the exact bytes the
        gate was checked against.

    Raises:
        ModelRetry: If the file is missing or fails :func:`check_current_read`.
    """
    resolved = resolve_path(deps.cwd, path)
    if not resolved.is_file():
        raise ModelRetry(not_found_message(resolved))
    data = resolved.read_bytes()
    check_current_read(deps, path, resolved, data, action)
    return resolved, data


def read_for_edit(deps: HarnessDeps, path: str) -> tuple[Path, str]:
    """Enforce the read gate on ``path`` and decode its gated bytes verbatim.

    The text is decoded from the same bytes the gate verified, with strict UTF-8
    and no newline translation, so existing CRLF/LF endings survive the round-trip.

    Returns:
        A ``(resolved, content)`` tuple: the canonical path and its exact text.

    Raises:
        ModelRetry: If the file is missing, fails the read gate, or is not valid UTF-8.
    """
    resolved, data = require_read(deps, path)
    try:
        return resolved, data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModelRetry(f"Cannot edit {path}: not valid UTF-8.") from exc


def read_verbatim(resolved: Path) -> str:
    """Read ``resolved`` exactly, preserving CRLF/LF endings.

    Uses a strict UTF-8 decode, so a non-UTF-8 file raises ``UnicodeDecodeError`` (the caller
    turns that into a user-facing refusal) instead of being silently repaired.
    """
    return resolved.read_text(encoding="utf-8", newline="")


def write_tracked(deps: HarnessDeps, resolved: Path, text: str) -> None:
    """Write ``text`` to ``resolved`` and record it as read at its new digest.

    The model authored ``text``, so it knows the file's exact content; recording it
    keeps the file editable after its own edits while leaving external changes
    detectable by the gate's digest check.
    """
    _ = resolved.write_text(text, encoding="utf-8", newline="")
    deps.read_files[resolved] = FileRead(digest=content_digest(text.encode("utf-8")))


def render_snippet(text: str, first_line: int, last_line: int) -> str:
    """Render a ``read``-format window of ``text`` around 1-indexed ``first_line``..``last_line``.

    The editing tools echo this back so the model can verify an edit's placement
    (and see fresh line numbers) without a follow-up ``read``. The window carries
    ``_SNIPPET_CONTEXT`` lines of context on each side and is clamped to the file;
    editing a file down to nothing yields a placeholder instead.
    """
    lines = text.splitlines()
    if not lines:
        return "(the file is now empty)"
    lo = max(1, min(first_line, len(lines)) - _SNIPPET_CONTEXT)
    hi = min(len(lines), max(last_line, first_line) + _SNIPPET_CONTEXT)
    return "\n".join(format_numbered_line(n, lines[n - 1]) for n in range(lo, hi + 1))


def staleness_notes(deps: HarnessDeps) -> list[str]:
    """Name tracked files whose bytes changed (or vanished) since the model saw them.

    Called by ``bash``/``python`` after a command runs, so external modifications
    surface immediately instead of as a later gate refusal. Each change is announced
    once per new content (remembered in ``FileRead.noted``); at most
    ``_MAX_STALE_NOTES`` files are named per scan, and any beyond that are counted
    here and named (once) on a later scan. Unreadable files are skipped; the digest
    gate still protects them at edit time.
    """
    notes: list[str] = []
    overflow = 0
    for resolved, record in sorted(deps.read_files.items()):
        if not resolved.is_file():
            if record.noted != _NOTED_DELETED:
                if len(notes) < _MAX_STALE_NOTES:
                    record.noted = _NOTED_DELETED
                    notes.append(f"[note: {resolved} was deleted during this command.]")
                else:
                    overflow += 1
            continue
        try:
            digest = content_digest(resolved.read_bytes())
        except OSError:
            continue
        if digest in (record.digest, record.noted):
            continue
        if len(notes) < _MAX_STALE_NOTES:
            record.noted = digest
            note = (
                f"[note: {resolved} changed on disk during this command; "
                + "read it again before editing.]"
            )
            notes.append(note)
        else:
            overflow += 1
    if overflow:
        notes.append(
            f"[note: {overflow} more tracked file(s) changed on disk during this command.]"
        )
    return notes
