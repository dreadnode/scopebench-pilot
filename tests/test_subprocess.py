"""Tests for the shared subprocess runner."""

import sys
from pathlib import Path

import pytest

from agent_harness import _subprocess


def test_captures_stdout(tmp_path: Path) -> None:
    result = _subprocess.run(["echo", "hi"], cwd=tmp_path, timeout=10)
    assert result.stdout.strip() == "hi"
    assert result.stderr == ""
    assert result.returncode == 0


def test_merge_stderr(tmp_path: Path) -> None:
    result = _subprocess.run(
        ["sh", "-c", "echo out; echo err 1>&2"],
        cwd=tmp_path,
        timeout=10,
        merge_stderr=True,
    )
    assert "out" in result.stdout
    assert "err" in result.stdout
    assert result.stderr == ""


def test_separate_stderr(tmp_path: Path) -> None:
    result = _subprocess.run(["sh", "-c", "echo err 1>&2"], cwd=tmp_path, timeout=10)
    assert result.stdout == ""
    assert "err" in result.stderr


def test_input_text(tmp_path: Path) -> None:
    result = _subprocess.run(
        [sys.executable, "-"],
        cwd=tmp_path,
        timeout=10,
        input_text="print('from stdin')",
    )
    assert "from stdin" in result.stdout


def test_env_layered(tmp_path: Path) -> None:
    result = _subprocess.run(
        ["sh", "-c", "echo $HARNESS_X"], cwd=tmp_path, timeout=10, env={"HARNESS_X": "yes"}
    )
    assert "yes" in result.stdout


def test_nonzero_returncode(tmp_path: Path) -> None:
    result = _subprocess.run(["sh", "-c", "exit 3"], cwd=tmp_path, timeout=10)
    assert result.returncode == 3


def test_timeout_terminates_on_sigterm(tmp_path: Path) -> None:
    with pytest.raises(TimeoutError) as exc:
        _ = _subprocess.run(["sleep", "5"], cwd=tmp_path, timeout=0.3)
    assert "Timed out" in str(exc.value)


def test_timeout_escalates_to_sigkill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_subprocess, "_KILL_GRACE_SECONDS", 0.3)
    with pytest.raises(TimeoutError):
        _ = _subprocess.run(["sh", "-c", "trap '' TERM; sleep 5"], cwd=tmp_path, timeout=0.3)


def test_non_utf8_stdout_is_replaced_not_raised(tmp_path: Path) -> None:
    # A command that emits raw non-UTF-8 bytes (a curl of a binary/compressed body, xxd,
    # openssl, a port scan) must not crash the runner: the bytes are decoded with
    # errors="replace" so the command still returns and the agent run stays gradeable
    # rather than aborting the whole trajectory on a UnicodeDecodeError.
    code = r'import sys; sys.stdout.buffer.write(b"before\xe7after")'
    result = _subprocess.run([sys.executable, "-c", code], cwd=tmp_path, timeout=10)
    assert result.stdout == "before�after"
    assert result.returncode == 0


def test_non_utf8_stderr_is_replaced_not_raised(tmp_path: Path) -> None:
    # The same tolerance must hold for stderr, which is decoded on its own in text mode.
    code = r'import sys; sys.stderr.buffer.write(b"oops\xe7")'
    result = _subprocess.run([sys.executable, "-c", code], cwd=tmp_path, timeout=10)
    assert result.stderr == "oops�"
    assert result.returncode == 0
