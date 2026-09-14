"""Tests for the multiedit tool."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.multiedit import multiedit

Writer = Callable[[str, str], Path]


def test_applies_sequential_edits(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "one two three")
    msg = multiedit(
        ctx,
        "f.txt",
        [
            {"old_string": "one", "new_string": "1"},
            {"old_string": "two", "new_string": "2"},
        ],
    )
    assert (ctx.deps.cwd / "f.txt").read_text() == "1 2 three"
    expected = (
        "The file f.txt has been updated. Applied 2 edit(s):\n"
        "1. 1 replacement(s)\n"
        "2. 1 replacement(s)"
    )
    assert msg == expected


def test_replace_all_flag(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "x x y")
    msg = multiedit(ctx, "f.txt", [{"old_string": "x", "new_string": "z", "replace_all": True}])
    assert (ctx.deps.cwd / "f.txt").read_text() == "z z y"
    assert "1. 2 replacement(s)" in msg


def test_fuzzy_edit_is_marked(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    # old_string is indented deeper than the file, so only the fuzzy fallback matches.
    seed_file("f.txt", "if x:\n  do()\n")
    msg = multiedit(ctx, "f.txt", [{"old_string": "    do()", "new_string": "    ok()"}])
    assert "1. 1 replacement(s) (matched after normalizing per-line whitespace)" in msg


def test_atomic_on_failure(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "keep")
    with pytest.raises(ModelRetry, match="Edit 2 of 2 failed: String to replace not found"):
        multiedit(
            ctx,
            "f.txt",
            [
                {"old_string": "keep", "new_string": "changed"},
                {"old_string": "nonexistent", "new_string": "x"},
            ],
        )
    assert (ctx.deps.cwd / "f.txt").read_text() == "keep"


def test_empty_edits_rejected(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    seed_file("f.txt", "data")
    with pytest.raises(ModelRetry, match="No edits provided"):
        multiedit(ctx, "f.txt", [])
    assert (ctx.deps.cwd / "f.txt").read_text() == "data"


def test_missing_file(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry, match="File does not exist"):
        multiedit(ctx, "nope.txt", [{"old_string": "a", "new_string": "b"}])


def test_requires_read(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("f.txt", "data")
    with pytest.raises(ModelRetry, match="has not been read yet"):
        multiedit(ctx, "f.txt", [{"old_string": "data", "new_string": "x"}])
