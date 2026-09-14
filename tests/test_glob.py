"""Tests for the glob tool."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.glob import glob

Writer = Callable[[str, str], Path]


def test_orders_by_mtime_desc(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    old = write_file("old.py", "a")
    new = write_file("new.py", "b")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    assert glob(ctx, "*.py").splitlines() == ["new.py", "old.py"]


def test_no_match(ctx: RunContext[HarnessDeps]) -> None:
    assert glob(ctx, "*.nomatch") == "No files found"


def test_path_given(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("sub/a.py", "x")
    assert glob(ctx, "*.py", path="sub") == "a.py"


def test_only_matches_files(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("pkg/mod.py", "x")
    write_file("top.py", "y")
    lines = glob(ctx, "*").splitlines()
    assert "top.py" in lines
    assert "pkg" not in lines


def test_truncates_at_cap(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    for i in range(101):
        write_file(f"m{i:03d}.py", "x")
    out = glob(ctx, "*.py")
    assert "... (1 more)" in out
    assert len(out.splitlines()) == 101


def test_invalid_pattern(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry):
        glob(ctx, "/abs/*")
