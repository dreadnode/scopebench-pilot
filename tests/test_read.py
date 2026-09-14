"""Tests for the read tool."""

import base64
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness._fs import LINE_TRUNCATED_MARKER, resolve_path
from agent_harness.deps import HarnessDeps, content_digest
from agent_harness.tools.read import read

Writer = Callable[[str, str], Path]


def test_reads_text_with_line_numbers(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "first\nsecond\n")
    out = read(ctx, "a.txt")
    assert "1\tfirst" in out
    assert "2\tsecond" in out


def test_offset_starts_partway(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("n.txt", "l1\nl2\nl3\n")
    out = read(ctx, "n.txt", offset=2)
    assert "2\tl2" in out
    assert "l1" not in out


def test_limit_caps_lines(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("n.txt", "l1\nl2\nl3\n")
    out = read(ctx, "n.txt", limit=1)
    assert "1\tl1" in out
    assert "l2" not in out


def test_limit_below_one_means_default(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("n.txt", "l1\nl2\nl3\n")
    out = read(ctx, "n.txt", limit=0)
    assert "1\tl1" in out
    assert "3\tl3" in out


def test_long_line_is_clipped_with_marker(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("long.txt", "y" * 3000)
    out = read(ctx, "long.txt")
    assert out.count("y") == 2000
    assert out.endswith(LINE_TRUNCATED_MARKER)


def test_large_output_is_truncated(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("big.txt", "\n".join("x" * 60 for _ in range(2000)))
    out = read(ctx, "big.txt")
    assert "output truncated" in out
    assert "of 2000 shown; continue with offset=" in out  # pagination is discoverable


def test_empty_file_returns_warning(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("empty.txt", "")
    assert read(ctx, "empty.txt") == "Warning: the file exists but the contents are empty."


def test_empty_file_with_offset_still_warns_empty(
    ctx: RunContext[HarnessDeps], write_file: Writer
) -> None:
    write_file("empty.txt", "")
    assert "contents are empty" in read(ctx, "empty.txt", offset=5)


def test_lists_directory(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("dir/sub/inner.txt", "x")
    write_file("dir/file.txt", "y")
    out = read(ctx, "dir")
    assert "file.txt" in out
    assert "sub/" in out


def test_reads_binary_as_base64(ctx: RunContext[HarnessDeps]) -> None:
    data = b"\x89PNG\r\n\x1a\nhello"
    (ctx.deps.cwd / "img.png").write_bytes(data)
    out = read(ctx, "img.png")
    assert base64.b64encode(data).decode("ascii") in out
    assert "base64-encoded png" in out


def test_reads_absolute_path(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    path = write_file("abs.txt", "hi")
    out = read(ctx, str(path))
    assert "1\thi" in out


def test_read_records_current_digest(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    path = write_file("r.txt", "data")
    _ = read(ctx, "r.txt")
    assert ctx.deps.read_files[path].digest == content_digest(b"data")


def test_empty_file_read_is_recorded(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    path = write_file("empty.txt", "")
    _ = read(ctx, "empty.txt")
    assert ctx.deps.read_files[path].digest == content_digest(b"")


def test_partial_read_records_current_digest(
    ctx: RunContext[HarnessDeps], write_file: Writer
) -> None:
    path = write_file("n.txt", "l1\nl2\nl3\n")
    _ = read(ctx, "n.txt", offset=2)
    assert ctx.deps.read_files[path].digest == content_digest(path.read_bytes())


def test_truncated_read_records_current_digest(
    ctx: RunContext[HarnessDeps], write_file: Writer
) -> None:
    path = write_file("big.txt", "\n".join("x" * 60 for _ in range(2000)))
    out = read(ctx, "big.txt")
    assert "output truncated" in out
    assert ctx.deps.read_files[path].digest == content_digest(path.read_bytes())


def test_read_past_end_warns_and_records_nothing(
    ctx: RunContext[HarnessDeps], write_file: Writer
) -> None:
    path = write_file("n.txt", "l1\nl2\nl3\n")
    out = read(ctx, "n.txt", offset=99)
    assert out == "Warning: the file is shorter than the provided offset (99); it has 3 lines."
    assert path not in ctx.deps.read_files


def test_read_past_end_keeps_existing_record(
    ctx: RunContext[HarnessDeps], write_file: Writer
) -> None:
    path = write_file("n.txt", "l1\nl2\nl3\n")
    _ = read(ctx, "n.txt")
    before = ctx.deps.read_files[path].digest
    _ = read(ctx, "n.txt", offset=99)
    assert ctx.deps.read_files[path].digest == before


def test_changed_file_carries_note_and_new_digest(
    ctx: RunContext[HarnessDeps], write_file: Writer
) -> None:
    path = write_file("n.txt", "l1\nl2\nl3\n")
    _ = read(ctx, "n.txt", limit=2)
    _ = path.write_text("L1\nL2\nL3\n")
    out = read(ctx, "n.txt", offset=3)
    assert "changed on disk" in out
    assert ctx.deps.read_files[path].digest == content_digest(b"L1\nL2\nL3\n")


def test_reread_of_unchanged_file_carries_no_note(
    ctx: RunContext[HarnessDeps], write_file: Writer
) -> None:
    write_file("n.txt", "l1\nl2\nl3\n")
    _ = read(ctx, "n.txt")
    assert "changed on disk" not in read(ctx, "n.txt")


def test_binary_not_recorded(ctx: RunContext[HarnessDeps]) -> None:
    (ctx.deps.cwd / "img.png").write_bytes(b"\x89PNG\r\n")
    _ = read(ctx, "img.png")
    assert resolve_path(ctx.deps.cwd, "img.png") not in ctx.deps.read_files


def test_large_binary_refused(ctx: RunContext[HarnessDeps]) -> None:
    (ctx.deps.cwd / "big.png").write_bytes(b"\x00" * 60_000)
    out = read(ctx, "big.png")
    assert "too large to inline" in out
    assert "base64" not in out


def test_not_found_suggests_similar(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("config.yaml", "x")
    with pytest.raises(ModelRetry) as exc:
        read(ctx, "confg.yaml")
    assert "Did you mean" in str(exc.value)
    assert "config.yaml" in str(exc.value)


def test_not_found_without_suggestion(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("apple.txt", "x")
    with pytest.raises(ModelRetry) as exc:
        read(ctx, "zzzzzzzz")
    assert "File does not exist" in str(exc.value)
    assert "Did you mean" not in str(exc.value)


def test_not_found_missing_parent(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry) as exc:
        read(ctx, "nope/here.txt")
    assert "File does not exist" in str(exc.value)
