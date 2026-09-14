"""Tests for the apply_patch tool and its patch parser."""

from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic_ai import ModelRetry, RunContext

from agent_harness._fs import join_preserving, resolve_path, split_preserving
from agent_harness._patch import FileOp, Hunk, PatchError, apply_hunks, parse_patch
from agent_harness.deps import HarnessDeps, content_digest
from agent_harness.tools.apply_patch import apply_patch

Writer = Callable[[str, str], Path]
Marker = Callable[[Path], None]


# ---------------------------------------------------------------- parse_patch


def test_parse_requires_begin() -> None:
    with pytest.raises(PatchError):
        parse_patch("")


def test_parse_wrong_begin() -> None:
    with pytest.raises(PatchError):
        parse_patch("nope\n*** End Patch")


def test_parse_requires_end() -> None:
    with pytest.raises(PatchError):
        parse_patch("*** Begin Patch\n*** Add File: a.txt\n+x")


def test_parse_add() -> None:
    ops = parse_patch("*** Begin Patch\n*** Add File: a.txt\n+l1\n+l2\n*** End Patch")
    assert ops[0].action == "add"
    assert ops[0].add_lines == ["l1", "l2"]


def test_parse_add_line_without_plus() -> None:
    with pytest.raises(PatchError):
        parse_patch("*** Begin Patch\n*** Add File: a.txt\nbadline\n*** End Patch")


def test_parse_update_with_hunk() -> None:
    patch = "*** Begin Patch\n*** Update File: a.txt\n@@\n ctx\n-old\n+new\n*** End Patch"
    ops = parse_patch(patch)
    assert ops[0].action == "update"
    assert ops[0].hunks[0].anchor is None
    assert ops[0].hunks[0].lines == [(" ", "ctx"), ("-", "old"), ("+", "new")]


def test_parse_hunk_anchor_captured() -> None:
    patch = "*** Begin Patch\n*** Update File: a.txt\n@@ def two\n-old\n+new\n*** End Patch"
    ops = parse_patch(patch)
    assert ops[0].hunks[0].anchor == "def two"


def test_parse_update_content_before_hunk() -> None:
    ops = parse_patch("*** Begin Patch\n*** Update File: a.txt\n-old\n+new\n*** End Patch")
    assert ops[0].hunks[0].lines == [("-", "old"), ("+", "new")]


def test_parse_invalid_hunk_line() -> None:
    with pytest.raises(PatchError):
        parse_patch("*** Begin Patch\n*** Update File: a.txt\nbadprefix\n*** End Patch")


def test_parse_empty_hunk_line() -> None:
    with pytest.raises(PatchError):
        parse_patch("*** Begin Patch\n*** Update File: a.txt\n@@\n\n*** End Patch")


def test_parse_delete() -> None:
    ops = parse_patch("*** Begin Patch\n*** Delete File: a.txt\n*** End Patch")
    assert ops[0].action == "delete"


def test_parse_move_after_update() -> None:
    patch = "*** Begin Patch\n*** Update File: a.txt\n*** Move to: b.txt\n-o\n+n\n*** End Patch"
    ops = parse_patch(patch)
    assert ops[0].move_to == "b.txt"


def test_parse_move_not_after_update() -> None:
    with pytest.raises(PatchError):
        parse_patch("*** Begin Patch\n*** Move to: b.txt\n*** End Patch")


def test_parse_hunk_outside_update() -> None:
    with pytest.raises(PatchError):
        parse_patch("*** Begin Patch\n@@\n*** End Patch")


def test_parse_content_outside_section() -> None:
    with pytest.raises(PatchError):
        parse_patch("*** Begin Patch\n+orphan\n*** End Patch")


# ---------------------------------------------------------------- apply_hunks


def test_apply_hunks_replaces() -> None:
    hunk = Hunk(lines=[(" ", "a"), ("-", "old"), ("+", "new"), (" ", "c")])
    assert apply_hunks("a\nold\nc\n", [hunk]) == "a\nnew\nc\n"


def test_apply_hunks_match_later() -> None:
    assert apply_hunks("header\ntarget\n", [Hunk(lines=[("-", "target")])]) == "header\n"


def test_apply_hunks_context_not_found() -> None:
    with pytest.raises(PatchError):
        apply_hunks("a\nb\n", [Hunk(lines=[("-", "missing")])])


def test_apply_hunks_pure_add_prepends() -> None:
    assert apply_hunks("x\n", [Hunk(lines=[("+", "top")])]) == "top\nx\n"


def test_apply_hunks_remove_all() -> None:
    assert apply_hunks("only\n", [Hunk(lines=[("-", "only")])]) == ""


def test_apply_hunks_anchor_disambiguates() -> None:
    content = "def one\n  x\n  y\ndef two\n  x\n  y\n"
    # The context (x, y) occurs under both methods; the anchor selects the second.
    hunk = Hunk(anchor="def two", lines=[(" ", "  x"), ("-", "  y"), ("+", "  z")])
    assert apply_hunks(content, [hunk]) == "def one\n  x\n  y\ndef two\n  x\n  z\n"


def test_apply_hunks_preserves_crlf() -> None:
    assert apply_hunks("a\r\nold\r\n", [Hunk(lines=[("-", "old")])]) == "a\r\n"


def test_apply_hunks_anchor_not_found_falls_back() -> None:
    # An anchor absent from the file falls back to searching from the top.
    hunk = Hunk(anchor="missing-anchor", lines=[("-", "old")])
    assert apply_hunks("a\nold\nb\n", [hunk]) == "a\nb\n"


# ---------------------------------------------------------------- tool


def test_tool_add(ctx: RunContext[HarnessDeps]) -> None:
    out = apply_patch(ctx, "*** Begin Patch\n*** Add File: new.txt\n+hello\n*** End Patch")
    assert out == "A new.txt"
    assert (ctx.deps.cwd / "new.txt").read_text() == "hello\n"


def test_tool_add_empty_file(ctx: RunContext[HarnessDeps]) -> None:
    out = apply_patch(ctx, "*** Begin Patch\n*** Add File: empty.txt\n*** End Patch")
    assert out == "A empty.txt"
    assert (ctx.deps.cwd / "empty.txt").read_text() == ""


def test_tool_add_exists(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("dup.txt", "x")
    with pytest.raises(ModelRetry):
        apply_patch(ctx, "*** Begin Patch\n*** Add File: dup.txt\n+y\n*** End Patch")


def test_tool_delete(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    path = seed_file("gone.txt", "x")
    out = apply_patch(ctx, "*** Begin Patch\n*** Delete File: gone.txt\n*** End Patch")
    assert out == "D gone.txt"
    assert not (ctx.deps.cwd / "gone.txt").exists()
    assert path not in ctx.deps.read_files  # a dropped record cannot go stale


def test_tool_delete_missing(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry):
        apply_patch(ctx, "*** Begin Patch\n*** Delete File: nope.txt\n*** End Patch")


def test_tool_update(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("u.txt", "a\nold\nc\n")
    patch = "*** Begin Patch\n*** Update File: u.txt\n@@\n a\n-old\n+new\n c\n*** End Patch"
    out = apply_patch(ctx, patch)
    assert out == "M u.txt"
    assert (ctx.deps.cwd / "u.txt").read_text() == "a\nnew\nc\n"


def test_tool_update_requires_read(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("u.txt", "a\nold\nc\n")  # written but not read
    patch = "*** Begin Patch\n*** Update File: u.txt\n@@\n-old\n+new\n*** End Patch"
    with pytest.raises(ModelRetry, match="has not been read yet"):
        apply_patch(ctx, patch)
    assert (ctx.deps.cwd / "u.txt").read_text() == "a\nold\nc\n"  # untouched


def test_tool_update_rejects_stale_read(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    path = seed_file("u.txt", "a\nold\nc\n")
    _ = path.write_text("a\nold\nc\nd\n")  # changed outside the tools since the read
    patch = "*** Begin Patch\n*** Update File: u.txt\n@@\n-old\n+new\n*** End Patch"
    with pytest.raises(ModelRetry, match="has been modified since it was read"):
        apply_patch(ctx, patch)
    assert (ctx.deps.cwd / "u.txt").read_text() == "a\nold\nc\nd\n"  # untouched


def test_tool_update_keeps_itself_fresh(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    # A second patch to the same file must not be refused as stale after the first.
    seed_file("u.txt", "one\n")
    first = "*** Begin Patch\n*** Update File: u.txt\n@@\n-one\n+two\n*** End Patch"
    second = "*** Begin Patch\n*** Update File: u.txt\n@@\n-two\n+three\n*** End Patch"
    _ = apply_patch(ctx, first)
    assert apply_patch(ctx, second) == "M u.txt"
    assert (ctx.deps.cwd / "u.txt").read_text() == "three\n"


def test_tool_update_missing(ctx: RunContext[HarnessDeps]) -> None:
    # An update to a file absent on disk is rejected before anything is applied.
    patch = "*** Begin Patch\n*** Update File: nope.txt\n@@\n-x\n+y\n*** End Patch"
    with pytest.raises(ModelRetry, match="File does not exist"):
        apply_patch(ctx, patch)


def test_tool_update_target_deleted_by_same_patch(
    ctx: RunContext[HarnessDeps], seed_file: Writer
) -> None:
    # The pre-flight sees the file, but an earlier op in the same patch removes it.
    seed_file("both.txt", "x\n")
    patch = (
        "*** Begin Patch\n*** Delete File: both.txt\n"
        "*** Update File: both.txt\n@@\n-x\n+y\n*** End Patch"
    )
    with pytest.raises(ModelRetry, match="Update File target not found"):
        apply_patch(ctx, patch)


def test_tool_update_non_utf8_refused(ctx: RunContext[HarnessDeps], mark_read: Marker) -> None:
    # An update to a non-UTF-8 file is refused (not silently corrupted).
    path = ctx.deps.cwd / "u.bin"
    path.write_bytes(b"\xff\xfe raw")
    mark_read(path)
    patch = "*** Begin Patch\n*** Update File: u.bin\n@@\n-raw\n+new\n*** End Patch"
    with pytest.raises(ModelRetry, match="UTF-8"):
        apply_patch(ctx, patch)
    assert path.read_bytes() == b"\xff\xfe raw"  # unchanged


def test_tool_update_preserves_crlf(ctx: RunContext[HarnessDeps], mark_read: Marker) -> None:
    path = ctx.deps.cwd / "u.txt"
    path.write_bytes(b"a\r\nold\r\nc\r\n")
    mark_read(path)
    patch = "*** Begin Patch\n*** Update File: u.txt\n@@\n-old\n+new\n*** End Patch"
    out = apply_patch(ctx, patch)
    assert out == "M u.txt"
    assert path.read_bytes() == b"a\r\nnew\r\nc\r\n"


def test_tool_update_move(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    src = seed_file("src.txt", "old\n")
    patch = (
        "*** Begin Patch\n*** Update File: src.txt\n*** Move to: dst.txt\n"
        "@@\n-old\n+new\n*** End Patch"
    )
    out = apply_patch(ctx, patch)
    assert out == "R src.txt -> dst.txt"
    assert not (ctx.deps.cwd / "src.txt").exists()
    assert (ctx.deps.cwd / "dst.txt").read_text() == "new\n"
    # The record follows the move: the source is forgotten, the target is known.
    assert src not in ctx.deps.read_files
    dst_record = ctx.deps.read_files[resolve_path(ctx.deps.cwd, "dst.txt")]
    assert dst_record.digest == content_digest(b"new\n")


def test_tool_malformed(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry):
        apply_patch(ctx, "garbage")


# ---------------------------------------------------------------- properties

# One "line" of content: excludes every character str.splitlines() breaks on
# (U+2028/U+2029 spelled via chr() to keep the source ASCII), so joining lines
# with a chosen terminator and re-splitting is bijective.
_SPLITLINES_CHARS = "\r\n\x0b\x0c\x1c\x1d\x1e\x85" + chr(0x2028) + chr(0x2029)
_LINE = st.text(alphabet=st.characters(exclude_characters=_SPLITLINES_CHARS))
# Paths and anchors survive the parser's .strip(): strip-stable and non-empty.
_TOKEN = _LINE.map(str.strip).filter(bool)
_NEWLINE = st.sampled_from(["\n", "\r\n"])
_HUNKS = st.builds(
    Hunk,
    anchor=st.none() | _TOKEN,
    lines=st.lists(st.tuples(st.sampled_from([" ", "-", "+"]), _LINE)),
)
_OPS = (
    st.builds(FileOp, action=st.just("add"), path=_TOKEN, add_lines=st.lists(_LINE))
    | st.builds(
        FileOp,
        action=st.just("update"),
        path=_TOKEN,
        move_to=st.none() | _TOKEN,
        hunks=st.lists(_HUNKS),
    )
    | st.builds(FileOp, action=st.just("delete"), path=_TOKEN)
)


def _render_patch(ops: list[FileOp]) -> str:
    """Serialize file operations back into the patch envelope (parse_patch's inverse)."""
    lines = ["*** Begin Patch"]
    for op in ops:
        if op.action == "add":
            lines.append(f"*** Add File: {op.path}")
            lines.extend("+" + text for text in op.add_lines)
        elif op.action == "update":
            lines.append(f"*** Update File: {op.path}")
            if op.move_to is not None:
                lines.append(f"*** Move to: {op.move_to}")
            for hunk in op.hunks:
                lines.append("@@" if hunk.anchor is None else f"@@ {hunk.anchor}")
                lines.extend(kind + text for kind, text in hunk.lines)
        else:
            lines.append(f"*** Delete File: {op.path}")
    lines.append("*** End Patch")
    return "\n".join(lines)


@given(ops=st.lists(_OPS))
def test_parse_round_trips_rendered_patches(ops: list[FileOp]) -> None:
    assert parse_patch(_render_patch(ops)) == ops


@given(lines=st.lists(_LINE), newline=_NEWLINE, trailing=st.booleans())
def test_deleting_every_line_empties_the_file(
    lines: list[str], newline: str, trailing: bool
) -> None:
    content = join_preserving(lines, newline, trailing=trailing)
    canonical, _, _ = split_preserving(content)
    hunk = Hunk(lines=[("-", text) for text in canonical])
    assert apply_hunks(content, [hunk]) == ""


@given(
    lines=st.lists(_LINE, min_size=1),
    newline=_NEWLINE,
    trailing=st.booleans(),
    index=st.integers(min_value=0),
    replacement=_LINE,
)
def test_replaces_first_occurrence_preserving_endings(
    lines: list[str], newline: str, trailing: bool, index: int, replacement: str
) -> None:
    content = join_preserving(lines, newline, trailing=trailing)
    canonical, nl, trail = split_preserving(content)
    assume(canonical)
    target = canonical[index % len(canonical)]
    expected = list(canonical)
    expected[canonical.index(target)] = replacement  # first occurrence wins
    hunk = Hunk(lines=[("-", target), ("+", replacement)])
    assert apply_hunks(content, [hunk]) == join_preserving(expected, nl, trailing=trail)


@given(
    lines=st.lists(_LINE),
    newline=_NEWLINE,
    trailing=st.booleans(),
    added=st.lists(_LINE, min_size=1),
)
def test_pure_add_hunk_prepends(
    lines: list[str], newline: str, trailing: bool, added: list[str]
) -> None:
    content = join_preserving(lines, newline, trailing=trailing)
    canonical, nl, trail = split_preserving(content)
    hunk = Hunk(lines=[("+", text) for text in added])
    assert apply_hunks(content, [hunk]) == join_preserving(added + canonical, nl, trailing=trail)
