"""Tests for the bash tool."""

import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness._subprocess import ProcResult
from agent_harness.deps import HarnessDeps
from agent_harness.tools.bash import bash

Writer = Callable[[str, str], Path]


def test_runs_command(ctx: RunContext[HarnessDeps]) -> None:
    assert bash(ctx, "echo hello").strip() == "hello"


def test_merges_stderr(ctx: RunContext[HarnessDeps]) -> None:
    out = bash(ctx, "echo out; echo err 1>&2")
    assert "out" in out
    assert "err" in out


def test_runs_in_cwd(ctx: RunContext[HarnessDeps]) -> None:
    _ = (ctx.deps.cwd / "marker.txt").write_text("x")
    assert "marker.txt" in bash(ctx, "ls")


def test_env_and_input(ctx: RunContext[HarnessDeps]) -> None:
    out = bash(ctx, "cat; echo $HARNESS_Y", env={"HARNESS_Y": "z"}, input="stdin-line\n")
    assert "stdin-line" in out
    assert "z" in out


def test_nonzero_returns_exit_marker(ctx: RunContext[HarnessDeps]) -> None:
    assert bash(ctx, "exit 7") == "[exit 7]"


def test_nonzero_includes_output(ctx: RunContext[HarnessDeps]) -> None:
    out = bash(ctx, "echo partial; exit 3")
    assert "partial" in out
    assert "[exit 3]" in out


def test_large_output_is_capped(ctx: RunContext[HarnessDeps]) -> None:
    out = bash(ctx, "echo HEAD; head -c 100000 /dev/zero | tr '\\0' X; echo; echo TAIL")
    assert "HEAD" in out  # head kept
    assert "TAIL" in out  # tail kept
    assert "truncated" in out
    assert len(out) < 60_000


def test_staleness_note_when_command_changes_read_file(
    ctx: RunContext[HarnessDeps], seed_file: Writer
) -> None:
    path = seed_file("f.txt", "v1")
    out = bash(ctx, f"echo v2 > '{path}'")
    assert f"[note: {path} changed on disk during this command" in out


def test_staleness_note_comes_after_exit_marker(
    ctx: RunContext[HarnessDeps], seed_file: Writer
) -> None:
    path = seed_file("f.txt", "v1")
    out = bash(ctx, f"echo v2 > '{path}'; exit 3")
    assert out.index("[exit 3]") < out.index("[note:")


def test_staleness_note_not_repeated(ctx: RunContext[HarnessDeps], seed_file: Writer) -> None:
    path = seed_file("f.txt", "v1")
    _ = bash(ctx, f"echo v2 > '{path}'")
    assert "[note:" not in bash(ctx, "true")


def test_timeout_raises_model_retry(
    ctx: RunContext[HarnessDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> ProcResult:
        raise TimeoutError("boom")

    # sys.modules gives the real module (the package attribute is the re-exported function).
    monkeypatch.setattr(sys.modules["agent_harness.tools.bash"], "run", fake_run)
    with pytest.raises(ModelRetry) as exc:
        _ = bash(ctx, "sleep 100")
    assert "boom" in str(exc.value)
