"""Tests for the write tool."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness._fs import resolve_path
from agent_harness.deps import HarnessDeps, content_digest
from agent_harness.tools.read import read
from agent_harness.tools.write import write

Writer = Callable[[str, str], Path]


def test_creates_file(ctx: RunContext[HarnessDeps]) -> None:
    msg = write(ctx, "new.txt", "hello")
    assert (ctx.deps.cwd / "new.txt").read_text() == "hello"
    assert msg == "File created successfully at: new.txt"


def test_overwrites_read_file(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("exist.txt", "old")
    msg = write(ctx, "exist.txt", "new")
    assert (ctx.deps.cwd / "exist.txt").read_text() == "new"
    assert msg == "File updated successfully at: exist.txt"


def test_overwrite_requires_read(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    # A blind full overwrite is the most destructive edit there is; it gets the
    # same gate as the surgical editors instead of a free pass.
    write_file("exist.txt", "precious")
    with pytest.raises(ModelRetry, match="Read it first before overwriting"):
        write(ctx, "exist.txt", "new")
    assert (ctx.deps.cwd / "exist.txt").read_text() == "precious"  # untouched


def test_overwrite_rejects_stale_read(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    path = seed_file("exist.txt", "old")
    _ = path.write_text("changed externally")
    with pytest.raises(ModelRetry, match="has been modified since it was read"):
        write(ctx, "exist.txt", "new")
    assert path.read_text() == "changed externally"  # untouched


def test_partial_read_unlocks_overwrite(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    # Any read at the current content satisfies the gate; full coverage is not required.
    write_file("exist.txt", "l1\nl2\nl3\n")
    _ = read(ctx, "exist.txt", limit=1)
    msg = write(ctx, "exist.txt", "new")
    assert msg == "File updated successfully at: exist.txt"


def test_rewrite_after_own_write(ctx: RunContext[HarnessDeps]) -> None:
    # A file the model just wrote is known; writing it again needs no read.
    _ = write(ctx, "w.txt", "v1")
    msg = write(ctx, "w.txt", "v2")
    assert (ctx.deps.cwd / "w.txt").read_text() == "v2"
    assert msg == "File updated successfully at: w.txt"


def test_creates_parents(ctx: RunContext[HarnessDeps]) -> None:
    _ = write(ctx, "a/b/c.txt", "x")
    assert (ctx.deps.cwd / "a" / "b" / "c.txt").read_text() == "x"


def test_registers_as_read(ctx: RunContext[HarnessDeps]) -> None:
    _ = write(ctx, "w.txt", "x")
    record = ctx.deps.read_files[resolve_path(ctx.deps.cwd, "w.txt")]
    assert record.digest == content_digest(b"x")
