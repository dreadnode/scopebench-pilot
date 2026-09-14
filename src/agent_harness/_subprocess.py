"""Shared subprocess runner for the bash and python tools.

The child runs in its own process group (``start_new_session=True``) so that a
timeout can reliably terminate the whole group: SIGTERM first, then SIGKILL
after a short grace period.
"""

import contextlib
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Seconds to wait after SIGTERM before escalating to SIGKILL on timeout.
_KILL_GRACE_SECONDS = 2.0


@dataclass
class ProcResult:
    """The captured result of a finished subprocess."""

    stdout: str
    stderr: str
    returncode: int


def run(
    args: list[str],
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    merge_stderr: bool = False,
) -> ProcResult:
    """Run ``args`` in a fresh process group and capture its output.

    Args:
        args: The command and arguments to execute (no shell is used directly).
        cwd: Working directory for the child process.
        timeout: Maximum seconds to allow before the process group is killed.
        env: Extra environment variables layered on top of the current environment.
        input_text: Text to send to the child's standard input, if any.
        merge_stderr: When true, stderr is merged into stdout.

    Returns:
        A :class:`ProcResult` with captured stdout, stderr, and the return code.

    Raises:
        TimeoutError: If the process does not finish within ``timeout`` seconds.
    """
    full_env = {**os.environ, **env} if env is not None else None
    stderr_dest = subprocess.STDOUT if merge_stderr else subprocess.PIPE
    proc: subprocess.Popen[str] = subprocess.Popen(
        args,
        cwd=cwd,
        env=full_env,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=stderr_dest,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    with proc:
        try:
            stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            stdout, stderr = _terminate(proc)
            partial = stdout + stderr
            msg = f"Timed out after {timeout:g}s. Partial output:\n{partial}"
            raise TimeoutError(msg) from None
    return ProcResult(stdout=stdout or "", stderr=stderr or "", returncode=proc.returncode or 0)


def _terminate(proc: subprocess.Popen[str]) -> tuple[str, str]:
    """SIGTERM then (after a grace period) SIGKILL the process group; reap output."""
    pgid = os.getpgid(proc.pid)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    try:
        stdout, stderr = proc.communicate(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
    return stdout or "", stderr or ""
