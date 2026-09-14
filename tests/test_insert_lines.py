"""Tests for the insert_lines tool."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.insert_lines import insert_lines

Writer = Callable[[str, str], Path]


def test_insert_at_start(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "b\nc\n")
    insert_lines(ctx, "f.txt", 1, "a")
    assert (ctx.deps.cwd / "f.txt").read_text() == "a\nb\nc\n"


def test_insert_middle(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a\nc\n")
    insert_lines(ctx, "f.txt", 2, "b")
    assert (ctx.deps.cwd / "f.txt").read_text() == "a\nb\nc\n"


def test_insert_past_end_appends(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a\n")
    insert_lines(ctx, "f.txt", 99, "b")
    assert (ctx.deps.cwd / "f.txt").read_text() == "a\nb\n"


def test_insert_multiline(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a\n")
    msg = insert_lines(ctx, "f.txt", 2, "x\ny")
    assert (ctx.deps.cwd / "f.txt").read_text() == "a\nx\ny\n"
    assert "2 line" in msg


def test_insert_into_empty_file(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "")
    insert_lines(ctx, "f.txt", 1, "only")
    assert (ctx.deps.cwd / "f.txt").read_text() == "only\n"


def test_insert_empty_into_empty(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "")
    insert_lines(ctx, "f.txt", 1, "")
    assert (ctx.deps.cwd / "f.txt").read_text() == ""


def test_preserves_crlf(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    path = seed_file("f.txt", "a\r\nb\r\n")
    insert_lines(ctx, "f.txt", 2, "x")
    assert path.read_bytes() == b"a\r\nx\r\nb\r\n"  # read_bytes: no newline translation


def test_preserves_missing_final_newline(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a\nb")  # no trailing newline
    insert_lines(ctx, "f.txt", 1, "x")
    assert (ctx.deps.cwd / "f.txt").read_text() == "x\na\nb"


def test_missing_file(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry):
        insert_lines(ctx, "nope.txt", 1, "x")


def test_requires_read(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("f.txt", "data")  # written but not read
    with pytest.raises(ModelRetry, match="has not been read yet"):
        insert_lines(ctx, "f.txt", 1, "x")
