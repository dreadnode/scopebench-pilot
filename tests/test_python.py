"""Tests for the python tool."""

import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness._subprocess import ProcResult
from agent_harness.deps import HarnessDeps
from agent_harness.tools.python import python

Writer = Callable[[str, str], Path]


def test_runs_code_returns_stdout(ctx: RunContext[HarnessDeps]) -> None:
    assert python(ctx, "print(1 + 2)").strip() == "3"


def test_stdout_and_stderr_surfaced(ctx: RunContext[HarnessDeps]) -> None:
    out = python(ctx, "import sys; print('out'); sys.stderr.write('err')")
    assert "out" in out
    assert "err" in out
    assert "[stderr]" in out


def test_runs_in_cwd(ctx: RunContext[HarnessDeps]) -> None:
    _ = (ctx.deps.cwd / "f.txt").write_text("data")
    out = python(ctx, "from pathlib import Path; print(Path('f.txt').read_text())")
    assert "data" in out


def test_env(ctx: RunContext[HarnessDeps]) -> None:
    out = python(ctx, "import os; print(os.environ['HARNESS_Z'])", env={"HARNESS_Z": "9"})
    assert "9" in out


def test_error_returns_traceback_and_exit(ctx: RunContext[HarnessDeps]) -> None:
    out = python(ctx, "raise ValueError('nope')")
    assert "nope" in out
    assert "[exit 1]" in out


def test_large_stdout_is_capped(ctx: RunContext[HarnessDeps]) -> None:
    out = python(ctx, "print('HEAD'); print('Z' * 100000); print('TAIL')")
    assert "HEAD" in out  # head kept
    assert "TAIL" in out  # tail kept
    assert "truncated" in out
    assert len(out) < 60_000


def test_staleness_note_when_code_changes_read_file(
    ctx: RunContext[HarnessDeps], seed_file: Writer
) -> None:
    path = seed_file("f.txt", "v1")
    code = f"from pathlib import Path; Path({str(path)!r}).write_text('v2')"
    out = python(ctx, code)
    assert f"[note: {path} changed on disk during this command" in out


def test_timeout_raises_model_retry(
    ctx: RunContext[HarnessDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> ProcResult:
        raise TimeoutError("slow")

    # sys.modules gives the real module (the package attribute is the re-exported function).
    monkeypatch.setattr(sys.modules["agent_harness.tools.python"], "run", fake_run)
    with pytest.raises(ModelRetry) as exc:
        _ = python(ctx, "while True: pass")
    assert "slow" in str(exc.value)
