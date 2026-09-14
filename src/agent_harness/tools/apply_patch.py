"""The ``apply_patch`` tool: apply a multi-file patch to the filesystem."""

from pathlib import Path

from pydantic_ai import ModelRetry, RunContext

from agent_harness._edit import read_verbatim, require_read, write_tracked
from agent_harness._fs import join_preserving, resolve_path
from agent_harness._patch import FileOp, PatchError, apply_hunks, parse_patch
from agent_harness.deps import HarnessDeps


def apply_patch(ctx: RunContext[HarnessDeps], patch_text: str) -> str:
    """Apply a multi-file patch to the filesystem.

    The patch is a ``*** Begin Patch`` / ``*** End Patch`` envelope containing
    ``*** Add File:``, ``*** Update File:`` (optionally with ``*** Move to:``),
    and ``*** Delete File:`` sections with ``@@`` context anchors. Every updated
    file must have been read, at its current on-disk content, first.

    Args:
        ctx: The tool run context (injected by the runtime).
        patch_text: The full patch envelope.

    Returns:
        A per-file status summary (``A``/``D``/``M``/``R`` and the path).

    Raises:
        ModelRetry: If the patch is malformed, an updated file was not read at
            its current content, or it cannot be applied.
    """
    try:
        ops = parse_patch(patch_text)
    except PatchError as exc:
        raise ModelRetry(str(exc)) from exc
    _require_reads(ctx, ops)
    try:
        results = [_apply_op(ctx.deps, op) for op in ops]
    except PatchError as exc:
        raise ModelRetry(str(exc)) from exc
    return "\n".join(results)


def _require_reads(ctx: RunContext[HarnessDeps], ops: list[FileOp]) -> None:
    """Reject the whole patch if any updated file is missing or not freshly read."""
    for op in ops:
        if op.action == "update":
            _ = require_read(ctx.deps, op.path)


def _apply_op(deps: HarnessDeps, op: FileOp) -> str:
    resolved = resolve_path(deps.cwd, op.path)
    if op.action == "add":
        return _apply_add(deps, resolved, op)
    if op.action == "delete":
        if not resolved.is_file():
            msg = f"Delete File target not found: {op.path}"
            raise PatchError(msg)
        resolved.unlink()
        _ = deps.read_files.pop(resolved, None)
        return f"D {op.path}"
    return _apply_update(deps, resolved, op)


def _apply_add(deps: HarnessDeps, resolved: Path, op: FileOp) -> str:
    if resolved.exists():
        msg = f"Add File target already exists: {op.path}"
        raise PatchError(msg)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    text = join_preserving(op.add_lines, "\n", trailing=True)
    write_tracked(deps, resolved, text)
    return f"A {op.path}"


def _apply_update(deps: HarnessDeps, resolved: Path, op: FileOp) -> str:
    if not resolved.is_file():
        msg = f"Update File target not found: {op.path}"
        raise PatchError(msg)
    # Verbatim read (newline="") + newline="" writes preserve existing CRLF/LF endings.
    try:
        original = read_verbatim(resolved)
    except UnicodeDecodeError as exc:
        msg = f"Cannot edit {op.path}: not valid UTF-8"
        raise PatchError(msg) from exc
    new_content = apply_hunks(original, op.hunks)
    if op.move_to is not None:
        target = resolve_path(deps.cwd, op.move_to)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_tracked(deps, target, new_content)
        resolved.unlink()
        _ = deps.read_files.pop(resolved, None)
        return f"R {op.path} -> {op.move_to}"
    write_tracked(deps, resolved, new_content)
    return f"M {op.path}"
