"""Tests for the edit_file tool."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.edit_file import edit_file
from agent_harness.tools.read import read

Writer = Callable[[str, str], Path]
Marker = Callable[[Path], None]

_SNIPPET_HEADER = "Here's the result of running `cat -n` on a snippet of the edited file:"


def test_replaces_unique_shows_snippet(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "hello world")
    msg = edit_file(ctx, "f.txt", "world", "there")
    assert (ctx.deps.cwd / "f.txt").read_text() == "hello there"
    assert msg.startswith(f"The file f.txt has been updated. {_SNIPPET_HEADER}")
    assert "1\thello there" in msg


def test_snippet_shows_context_window(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "\n".join(f"l{n}" for n in range(1, 13)))
    lines = edit_file(ctx, "f.txt", "l6", "SIX").splitlines()
    assert lines[1] == "2\tl2"  # four lines of context each side of the edit
    assert lines[-1] == "10\tl10"
    assert "6\tSIX" in lines


def test_snippet_clamped_at_file_bounds(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a\nb\nc")
    lines = edit_file(ctx, "f.txt", "b", "B").splitlines()
    assert lines[1] == "1\ta"
    assert lines[-1] == "3\tc"


def test_edit_emptying_file_shows_placeholder(
    ctx: RunContext[HarnessDeps], seed_file: Writer
) -> None:
    seed_file("f.txt", "only")
    msg = edit_file(ctx, "f.txt", "only", "")
    assert "(the file is now empty)" in msg
    assert (ctx.deps.cwd / "f.txt").read_text() == ""


def test_replace_all_message(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a a a")
    msg = edit_file(ctx, "f.txt", "a", "b", replace_all=True)
    assert (ctx.deps.cwd / "f.txt").read_text() == "b b b"
    assert msg == "The file f.txt has been updated. All occurrences were successfully replaced."


def test_fuzzy_match_is_surfaced(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    # old_string is indented deeper than the file, so only the line-trimmed
    # fallback can find it (a shallower old_string would exact-match as a substring).
    seed_file("f.txt", "if x:\n  do()\n")
    msg = edit_file(ctx, "f.txt", "    do()", "    done()")
    assert "(old_string matched after normalizing per-line whitespace)" in msg
    assert (ctx.deps.cwd / "f.txt").read_text() == "if x:\n  done()"


def test_ambiguous_names_match_lines(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "x\ny\nx\n")
    with pytest.raises(ModelRetry) as exc:
        edit_file(ctx, "f.txt", "x", "z")
    msg = str(exc.value)
    assert "Found 2 matches of the string to replace, but replace_all is false." in msg
    assert "Matches found at lines: 1, 3." in msg


def test_not_found_string(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "abc")
    with pytest.raises(ModelRetry) as exc:
        edit_file(ctx, "f.txt", "zzz", "q")
    assert str(exc.value) == "String to replace not found in file.\nString: zzz"


def test_not_found_hints_at_read_prefixes(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "foo\nbar\n")
    with pytest.raises(ModelRetry, match="line-number prefixes"):
        edit_file(ctx, "f.txt", "1\tfoo\n2\tbar", "x")


def test_noop_edit_rejected(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "same")
    with pytest.raises(ModelRetry, match="No changes to make"):
        edit_file(ctx, "f.txt", "same", "same")
    assert (ctx.deps.cwd / "f.txt").read_text() == "same"


def test_missing_file(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry, match="File does not exist"):
        edit_file(ctx, "nope.txt", "a", "b")


def test_requires_read_first(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("f.txt", "content")
    with pytest.raises(ModelRetry, match="has not been read yet"):
        edit_file(ctx, "f.txt", "content", "new")


def test_rejects_stale_read(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    # A file changed outside the edit tools (bash, another process, ...) must be
    # re-read before it can be edited again.
    path = seed_file("f.txt", "original")
    _ = path.write_text("changed externally")
    with pytest.raises(ModelRetry, match="has been modified since it was read"):
        edit_file(ctx, "f.txt", "changed", "x")
    assert path.read_text() == "changed externally"  # untouched


def test_own_edits_stay_fresh(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    # An edit made through the tool must not trip the staleness check for the next one.
    seed_file("f.txt", "one two")
    _ = edit_file(ctx, "f.txt", "one", "1")
    _ = edit_file(ctx, "f.txt", "two", "2")
    assert (ctx.deps.cwd / "f.txt").read_text() == "1 2"


def test_partial_read_unlocks_editing(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    # Any read at the current content satisfies the gate; full coverage is not required.
    write_file("f.txt", "l1\nl2\nl3\nl4\n")
    _ = read(ctx, "f.txt", limit=2)
    msg = edit_file(ctx, "f.txt", "l4", "x")
    assert "has been updated" in msg


def test_truncated_read_unlocks_editing(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    # A file whose rendering exceeds one read's output cap is editable after that
    # one (truncated) read — the gate needs freshness, not full coverage.
    write_file("big.txt", "\n".join(f"line {n} " + "x" * 60 for n in range(1, 2001)))
    out = read(ctx, "big.txt")
    assert "output truncated" in out
    _ = edit_file(ctx, "big.txt", "line 1999 ", "LAST ")
    assert "LAST " in (ctx.deps.cwd / "big.txt").read_text()


def test_rejects_non_utf8(ctx: RunContext[HarnessDeps], mark_read: Marker) -> None:
    # A file `read` showed lossily is refused for editing, not silently corrupted.
    path = ctx.deps.cwd / "b.bin"
    path.write_bytes(b"\xff\xfe not utf-8")
    mark_read(path)
    with pytest.raises(ModelRetry, match="UTF-8"):
        edit_file(ctx, "b.bin", "not", "X")
    assert path.read_bytes() == b"\xff\xfe not utf-8"  # unchanged


def test_preserves_crlf_and_snippet_has_no_cr(
    ctx: RunContext[HarnessDeps], write_file: Writer, mark_read: Marker
) -> None:
    # Editing one line must not rewrite the whole file's CRLF endings to LF, and
    # the echoed snippet renders per line (no raw carriage returns).
    path = write_file("crlf.txt", "")
    path.write_bytes(b"one\r\ntwo\r\nthree\r\n")
    mark_read(path)
    msg = edit_file(ctx, "crlf.txt", "two", "TWO")
    assert path.read_bytes() == b"one\r\nTWO\r\nthree\r\n"
    assert "\r" not in msg
    assert "2\tTWO" in msg
