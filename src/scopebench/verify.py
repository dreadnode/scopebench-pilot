"""Capture a task's outcome after the agent finishes: read the flag, run the verifier.

Where the flag lands depends on the strategy, so reading it does too:

* **harness** — the agent ran in a container and its runtime copied the flag to ``/work/result.txt``
  (the mounted workspace). We read only the workspace, never the host ``/tmp`` (which is a different
  namespace and could hold a stale flag from a host-side run).
* **solution / dry-run** — the reference script ran on the host and wrote the shared host
  ``/tmp/result.txt``; we include that.

For ``method: script`` the same split decides where the verifier runs. A ``where: agent`` verifier
after a *harness* run executes in a throwaway ``python:3.11-slim`` container on the task's backend
network (services by DNS, workspace mounted at ``/work``), mirroring the reference platform.
Otherwise the verifier runs on the host, as before.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_sandbox._docker import remove_container
from scopebench.manifest import FlagVerification, ScriptVerification, Verification

# Where a flag may land when a host-side script writes it (the tasks' own /tmp contract). The
# in-container ScopeJudge runtime names the same path (scopejudge.runtime._FLAG_SRC); the two are
# separate processes and deliberately share no code, so a parity test keeps them in lockstep.
_DEFAULT_FLAG_PATHS = ("/tmp/result.txt",)  # noqa: S108 - the tasks' own contract
_VERIFIER_IMAGE = "python:3.11-slim"
_VERIFIER_GRACE_SEC = 15


@dataclass(frozen=True)
class VerifyOutcome:
    """What we captured after a task.

    Attributes:
        verified: The verifier's pass/fail for ``script`` tasks; ``None`` for ``flag`` tasks,
            which are graded by the evaluator instead.
        detail: Human-readable explanation for the report.
        submitted: The flag text found, when applicable.
    """

    verified: bool | None
    detail: str
    submitted: str | None = None


class VerificationError(RuntimeError):
    """Verifier infrastructure failed, so the run cannot be graded safely."""


def flag_paths(
    verification: FlagVerification, workspace: Path, *, include_shared_tmp: bool
) -> list[Path]:
    """The paths a flag for this task might occupy.

    The workspace copy is always included; the shared host ``/tmp`` (and ``verification.path``) are
    included only for host-side runs, where a script wrote them directly.
    """
    paths: list[Path] = [workspace / "result.txt"]
    if include_shared_tmp:
        if verification.path:
            paths.append(Path(verification.path))
        paths.extend(Path(p) for p in _DEFAULT_FLAG_PATHS)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def clean_flag_targets(verification: Verification, workspace: Path) -> None:
    """Remove any pre-existing flag files (host and workspace) so a stale result cannot pass."""
    if not isinstance(verification, FlagVerification):
        return
    for path in flag_paths(verification, workspace, include_shared_tmp=True):
        # missing, permission, or a directory in the way — all fine to skip
        with contextlib.suppress(OSError):
            path.unlink()


def read_flag(
    verification: FlagVerification, workspace: Path, *, include_shared_tmp: bool
) -> str | None:
    """Return the produced flag text (stripped) from the first path that exists."""
    for path in flag_paths(verification, workspace, include_shared_tmp=include_shared_tmp):
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    return None


def run_verifier(
    verification: ScriptVerification,
    task_dir: Path,
    workspace: Path,
    env: dict[str, str],
) -> VerifyOutcome:
    """Run a ``method: script`` verifier on the host and grade by exit code.

    Executed from the agent workspace (so its ``result.txt`` / cwd probes resolve there) with the
    host service-URL environment injected.
    """
    script_path = task_dir / verification.script
    if not script_path.is_file():
        return VerifyOutcome(verified=False, detail=f"verifier not found: {script_path}")

    try:
        proc = subprocess.run(  # noqa: S603 - task-supplied verifier, run intentionally
            ["bash", str(script_path)],  # noqa: S607 - bash resolved via PATH by design
            cwd=str(workspace),
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=verification.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError(f"verifier timed out ({verification.timeout}s)") from exc
    except OSError as exc:
        raise VerificationError(f"could not run verifier: {exc}") from exc
    return _verifier_outcome(proc.returncode, proc.stdout, proc.stderr)


def run_verifier_container(
    verification: ScriptVerification,
    task_dir: Path,
    workspace: Path,
    env: dict[str, str],
    backend_net: str,
) -> VerifyOutcome:
    """Run a ``where: agent`` verifier in a backend-network container (workspace at /work)."""
    script_path = task_dir / verification.script
    if not script_path.is_file():
        return VerifyOutcome(verified=False, detail=f"verifier not found: {script_path}")

    container = f"{workspace.name}-verify"
    cleanup_error = remove_container(container)
    if cleanup_error is not None:
        raise VerificationError(f"could not prepare verifier container: {cleanup_error}")
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        container,
        "--network",
        backend_net,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{workspace.resolve()}:/work",
        "-w",
        "/work",
    ]
    for key, value in env.items():
        cmd.extend(["-e", f"{key}={value}"])
    cmd.extend([_VERIFIER_IMAGE, "bash", "-s"])
    try:
        proc = subprocess.run(  # noqa: S603 - fixed docker CLI, verifier source on stdin
            cmd,
            input=script_path.read_text(encoding="utf-8"),
            capture_output=True,
            text=True,
            timeout=verification.timeout + _VERIFIER_GRACE_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cleanup_error = remove_container(container)
        detail = f"verifier timed out ({verification.timeout}s)"
        if cleanup_error is not None:
            detail += f"; cleanup failed: {cleanup_error}"
        raise VerificationError(detail) from exc
    except OSError as exc:
        raise VerificationError(f"could not run verifier container: {exc}") from exc

    outcome = _verifier_outcome(proc.returncode, proc.stdout, proc.stderr)
    if proc.returncode != 0:
        cleanup_error = remove_container(container)
        if cleanup_error is not None:
            raise VerificationError(f"{outcome.detail}; cleanup failed: {cleanup_error}")
    return outcome


def _verifier_outcome(returncode: int, stdout: str, stderr: str) -> VerifyOutcome:
    lines = (stdout or stderr).strip().splitlines()[-1:] or ["(no verifier output)"]
    return VerifyOutcome(verified=returncode == 0, detail=f"verifier exit {returncode}: {lines[0]}")


def collect(
    verification: Verification,
    task_dir: Path,
    workspace: Path,
    script_env: dict[str, str],
    network_env: dict[str, str],
    backend_net: str,
    *,
    agent_in_container: bool,
) -> VerifyOutcome:
    """Capture a task's outcome using its declared verification method.

    ``flag`` tasks are only *read* here (the evaluator grades them). ``script`` tasks are executed:
    in a backend-network container for a ``where: agent`` verifier after a harness run, otherwise on
    the host.
    """
    if isinstance(verification, FlagVerification):
        submitted = read_flag(verification, workspace, include_shared_tmp=not agent_in_container)
        detail = "flag captured" if submitted is not None else "no result file was written"
        return VerifyOutcome(verified=None, detail=detail, submitted=submitted)
    if verification.where == "agent" and agent_in_container:
        return run_verifier_container(verification, task_dir, workspace, network_env, backend_net)
    return run_verifier(verification, task_dir, workspace, script_env)
