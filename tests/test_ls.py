"""Tests for the ls tool."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.ls import ls

Writer = Callable[[str, str], Path]


def test_tree_view(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("root.txt", "a")
    write_file("sub/inner.txt", "b")
    out = ls(ctx)
    assert "root.txt" in out
    assert "sub/" in out
    assert "  inner.txt" in out


def test_explicit_path(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("d/x.txt", "a")
    assert "x.txt" in ls(ctx, path="d")


def test_ignores_default_and_named(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file(".git/config", "x")
    write_file("build", "x")
    write_file("keep.txt", "y")
    out = ls(ctx)
    assert "keep.txt" in out
    assert ".git" not in out
    assert "build" not in out.splitlines()


def test_extra_ignore(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("skip.txt", "a")
    write_file("keep.txt", "b")
    out = ls(ctx, ignore=["skip.txt"])
    assert "keep.txt" in out
    assert "skip.txt" not in out


def test_not_a_directory(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("file.txt", "a")
    with pytest.raises(ModelRetry):
        ls(ctx, path="file.txt")


def test_truncates_at_cap(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    for i in range(101):
        write_file(f"f{i:03d}.txt", "x")
    assert "truncated at 100" in ls(ctx)
