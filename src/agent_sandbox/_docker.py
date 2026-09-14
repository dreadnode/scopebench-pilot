"""Small, bounded Docker cleanup primitives shared by sandbox container runners."""

from __future__ import annotations

import subprocess

_REMOVE_TIMEOUT_SEC = 15.0
_ABSENT_MARKER = "no such container"


def remove_container(container: str, *, timeout: float = _REMOVE_TIMEOUT_SEC) -> str | None:
    """Force-remove ``container``; return a diagnostic, or ``None`` when it is absent/removed."""
    cmd = ["docker", "rm", "-f", container]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed docker CLI, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"`docker rm -f {container}` timed out after {timeout:.0f}s"
    except OSError as exc:
        return f"could not run `docker rm -f {container}`: {exc}"

    detail = (proc.stderr or proc.stdout).strip()
    if proc.returncode == 0 or _ABSENT_MARKER in detail.casefold():
        return None
    return f"`docker rm -f {container}` failed: {detail or 'no command output'}"
