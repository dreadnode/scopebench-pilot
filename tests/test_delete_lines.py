"""Tests for the delete_lines tool."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.delete_lines import delete_lines

Writer = Callable[[str, str], Path]


def test_delete_range(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a\nb\nc\nd\n")
    msg = delete_lines(ctx, "f.txt", 2, 3)
    assert (ctx.deps.cwd / "f.txt").read_text() == "a\nd\n"
    assert "2 line" in msg


def test_delete_all(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a\nb\n")
    delete_lines(ctx, "f.txt", 1, 2)
    assert (ctx.deps.cwd / "f.txt").read_text() == ""


def test_preserves_crlf(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    path = seed_file("f.txt", "a\r\nb\r\nc\r\n")
    delete_lines(ctx, "f.txt", 2, 2)
    assert path.read_bytes() == b"a\r\nc\r\n"  # read_bytes: no newline translation


def test_start_below_one(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a\nb\n")
    with pytest.raises(ModelRetry):
        delete_lines(ctx, "f.txt", 0, 1)


def test_end_beyond_length(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a\nb\n")
    with pytest.raises(ModelRetry):
        delete_lines(ctx, "f.txt", 1, 99)


def test_start_after_end(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "a\nb\nc\n")
    with pytest.raises(ModelRetry):
        delete_lines(ctx, "f.txt", 3, 2)


def test_missing_file(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry):
        delete_lines(ctx, "nope.txt", 1, 1)


def test_requires_read(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("f.txt", "a\nb\n")  # written but not read
    with pytest.raises(ModelRetry, match="has not been read yet"):
        delete_lines(ctx, "f.txt", 1, 1)
