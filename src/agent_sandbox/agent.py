"""Run ScopeJudge sealed in a hardened container and return its raw result.

This is the host side of the sandbox: it shells out to ``docker run`` with the hardening flags,
pipes the run config (including the API key) in on **stdin** — never argv/env — and validates the
container's ``/work/agent-result.json`` into an :class:`AgentResult`. It imports no
``agent_harness``/``pydantic_ai``; the in-container :mod:`scopejudge.runtime` application owns the
agent loop. It computes no scope/grading logic — that stays with the caller (the benchmark).
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from agent_sandbox._docker import remove_container
from scopejudge.protocol import AgentConfig, AgentResult, ToolCall

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ("AgentConfig", "AgentResult", "ToolCall", "run_agent_container")

# The container writes its result JSON here (a host-visible workspace file).
_RESULT_FILE = "agent-result.json"
_RUN_OUTPUTS = (_RESULT_FILE, "result.txt", "trajectory.json")
# Extra wall-clock beyond the agent's own budget for the container to write its result and exit
# before the outer `docker run` is force-killed.
_CONTAINER_GRACE_SEC = 60.0


# Validate the shared ScopeJudge protocol directly. Optional result fields retain their dataclass
# defaults when an older or partially-written result omits them.
_RESULT_ADAPTER: TypeAdapter[AgentResult] = TypeAdapter(AgentResult)


def run_agent_container(
    *,
    image: str,
    network: str,
    workspace: Path,
    config: AgentConfig,
    name: str,
) -> AgentResult:
    """Run ``image`` sealed on ``network``, config on stdin; return the parsed :class:`AgentResult`.

    The container is capability-dropped, resource-limited, and mounts only ``workspace`` at
    ``/work`` (no host filesystem, no docker socket). It runs as the calling host UID/GID so it can
    write outputs without weakening the workspace's host permissions.
    """
    cleanup_error = remove_container(name)
    if cleanup_error is not None:
        return AgentResult(output="", error=f"could not prepare agent container: {cleanup_error}")
    output_error = _clear_run_outputs(workspace)
    if output_error is not None:
        return AgentResult(output="", error=output_error)

    cmd = _run_cmd(image, name, network, workspace)
    try:
        proc = subprocess.run(  # noqa: S603 - fixed docker CLI, no shell
            cmd,
            input=json.dumps(config),
            capture_output=True,
            text=True,
            timeout=config["timeout"] + _CONTAINER_GRACE_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired:
        budget = config["timeout"] + _CONTAINER_GRACE_SEC
        cleanup_error = remove_container(name)
        detail = f"agent container exceeded {budget:.0f}s budget"
        if cleanup_error is not None:
            detail += f"; cleanup failed: {cleanup_error}"
        return AgentResult(output="", error=detail)
    except OSError as exc:
        return AgentResult(output="", error=f"could not run agent container: {exc}")

    result = _parse_result(workspace, proc)
    if proc.returncode != 0:
        cleanup_error = remove_container(name)
        if cleanup_error is not None and result.error is not None:
            result.error += f"; cleanup failed: {cleanup_error}"
    return result


def _clear_run_outputs(workspace: Path) -> str | None:
    """Remove output files from an earlier run so only fresh container results can be parsed."""
    for name in _RUN_OUTPUTS:
        path = workspace / name
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            return f"could not remove stale run output {path}: {exc}"
    return None


def _run_cmd(image: str, container: str, network: str, workspace: Path) -> list[str]:
    """The hardened ``docker run`` command: sealed from the host, only /work mounted."""
    return [
        "docker",
        "run",
        "-i",
        "--rm",
        "--name",
        container,
        "--network",
        network,
        "--hostname",
        "ops-01",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "512",
        "--memory",
        "2g",
        "--cpus",
        "2",
        "-v",
        f"{workspace.resolve()}:/work",
        "-w",
        "/work",
        image,
    ]


def _parse_result(workspace: Path, proc: subprocess.CompletedProcess[str]) -> AgentResult:
    """Validate the container's result JSON into an :class:`AgentResult` (no grading).

    The result file lives on the agent-writable ``/work`` mount, so it is never trusted: a
    missing, unreadable, or non-conforming file degrades into an error result instead of
    raising, keeping the run gradeable (flag verification is independent ground truth).
    """
    result_path = workspace / _RESULT_FILE
    if not result_path.is_file():
        detail = (proc.stderr or proc.stdout).strip()[-400:]
        return AgentResult(
            output="",
            error=f"agent produced no result (docker exit {proc.returncode}): {detail}",
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[-400:] or "no command output"
        return AgentResult(
            output="",
            error=f"agent container exited {proc.returncode}: {detail}",
        )
    try:
        # read_bytes + validate_json: bad JSON, a bad shape, and invalid UTF-8 all surface as
        # ValidationError rather than as three separate exception types.
        result = _RESULT_ADAPTER.validate_json(result_path.read_bytes())
    except ValidationError as exc:
        return AgentResult(output="", error=f"malformed agent result: {exc}")
    except OSError as exc:
        return AgentResult(output="", error=f"malformed agent result: {type(exc).__name__}: {exc}")
    return result
