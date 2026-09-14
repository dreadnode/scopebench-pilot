"""Tests for the grep tool."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.grep import grep

Writer = Callable[[str, str], Path]


def test_content_basic(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "alpha\nbeta\ngamma\n")
    out = grep(ctx, "beta")
    assert "a.txt:" in out
    assert "2: beta" in out


def test_content_with_context(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "l1\nMATCH\nl3\n")
    out = grep(ctx, "MATCH", context=1)
    assert "1: l1" in out
    assert "2: MATCH" in out
    assert "3: l3" in out


def test_content_no_match(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "nothing\n")
    assert grep(ctx, "zzz") == "No matches found"


def test_content_limit(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "hit\nhit\nhit\n")
    out = grep(ctx, "hit", limit=1)
    assert out.count("hit") == 1


def test_content_skips_nonmatching_file(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("match.txt", "yes\n")
    write_file("nomatch.txt", "no\n")
    out = grep(ctx, "yes")
    assert "match.txt:" in out
    assert "nomatch.txt" not in out


def test_files_mode(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "needle\n")
    write_file("b.txt", "haystack\n")
    out = grep(ctx, "needle", output_mode="files")
    assert "a.txt" in out
    assert "b.txt" not in out


def test_files_mode_no_match(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "x\n")
    assert grep(ctx, "zzz", output_mode="files") == "No matches found"


def test_files_mode_limit(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "m\n")
    write_file("b.txt", "m\n")
    assert len(grep(ctx, "m", output_mode="files", limit=1).splitlines()) == 1


def test_count_mode(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "x\nx\ny\n")
    assert "a.txt: 2" in grep(ctx, "x", output_mode="count")


def test_count_mode_no_match(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "y\n")
    assert grep(ctx, "zzz", output_mode="count") == "No matches found"


def test_count_mode_limit(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "m\n")
    write_file("b.txt", "m\n")
    assert len(grep(ctx, "m", output_mode="count", limit=1).splitlines()) == 1


def test_include_filter(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.py", "target\n")
    write_file("a.txt", "target\n")
    out = grep(ctx, "target", output_mode="files", include="*.py")
    assert "a.py" in out
    assert "a.txt" not in out


def test_ignore_case(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("a.txt", "HELLO\n")
    assert "a.txt" in grep(ctx, "hello", ignore_case=True, output_mode="files")


def test_literal(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("dot.txt", "a.b\n")
    write_file("any.txt", "axb\n")
    out = grep(ctx, "a.b", literal=True, output_mode="files")
    assert "dot.txt" in out
    assert "any.txt" not in out


def test_single_file(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("only.txt", "found\n")
    assert grep(ctx, "found", path="only.txt", output_mode="files") == "only.txt"


def test_skips_ignored_dirs_and_files(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file(".git/config", "secret\n")
    write_file("build", "secret\n")
    write_file("keep.txt", "secret\n")
    out = grep(ctx, "secret", output_mode="files")
    assert "keep.txt" in out
    assert ".git" not in out
    assert "build" not in out.splitlines()


def test_content_clips_long_line(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("big.txt", "PREFIX" + "y" * 100000 + "\n")
    out = grep(ctx, "PREFIX")
    assert "PREFIX" in out
    assert out.count("y") <= 2000  # clipped to MAX_LINE_CHARS


def test_skips_binary_files(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("data.png", "secret\n")  # binary suffix -> skipped in the walk
    write_file("keep.txt", "secret\n")
    out = grep(ctx, "secret", output_mode="files")
    assert "keep.txt" in out
    assert "data.png" not in out


def test_invalid_regex(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry):
        grep(ctx, "[")


def test_path_not_found(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry):
        grep(ctx, "x", path="nope")
