"""Build (once per invocation) and reuse the ScopeJudge sandbox image.

The image is tagged by a content hash of the baseline ``agent_harness`` source, the in-container
``scopejudge`` application, the Dockerfile, and locked dependencies. The build context contains
only those inputs — never the repository root, tasks, secrets, or prior runs.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

# image.py -> agent_sandbox -> src -> repo root (where pyproject.toml / uv.lock live).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
_DOCKERFILE = _RUNTIME_DIR / "Dockerfile"
_LIB_SRC = _REPO_ROOT / "src" / "agent_harness"
_SCOPEJUDGE_SRC = _REPO_ROOT / "src" / "scopejudge"
_IMAGE_PREFIX = "agent-sandbox"


class ImageError(RuntimeError):
    """Building the agent image failed."""


def _locked_requirements() -> str:
    """Export agent-harness's runtime dependency closure, pinned exactly as ``uv.lock`` resolves it.

    ``--locked`` refuses a lockfile that has drifted from ``pyproject.toml`` (loud failure over
    silently baking stale pins); ``--no-default-groups`` drops the dev/bench groups; the project
    itself is left out because the Dockerfile puts the source on ``PYTHONPATH`` instead. Hashes
    stay in, so the in-image ``pip install`` is hash-checked end to end.
    """
    proc = subprocess.run(
        [  # noqa: S607 - fixed uv CLI resolved via PATH by design, like docker below; no shell
            "uv",
            "export",
            "--locked",
            "--no-default-groups",
            "--no-emit-project",
            "--no-header",
            "--no-annotate",
            "--format",
            "requirements-txt",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise ImageError(f"exporting pinned dependencies from uv.lock failed: {detail}")
    return proc.stdout


def _content_tag(requirements: str) -> str:
    """Hash every source file that can affect the in-container application."""
    digest = hashlib.sha256()
    for source in (_LIB_SRC, _SCOPEJUDGE_SRC):
        for path in sorted(source.rglob("*.py")):
            if "tests" in path.relative_to(source).parts:
                continue
            digest.update(path.relative_to(_REPO_ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    digest.update(_DOCKERFILE.read_bytes())
    digest.update(requirements.encode())
    return digest.hexdigest()[:12]


def _stage_context(stage: Path, requirements: str) -> None:
    """Materialize only the baseline library, ScopeJudge app, Dockerfile inputs, and pins."""
    for source in (_LIB_SRC, _SCOPEJUDGE_SRC):
        _ = shutil.copytree(
            source,
            stage / "src" / source.name,
            ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"),
        )
    _ = (stage / "requirements.txt").write_text(requirements, encoding="utf-8")


def _image_exists(tag: str) -> bool:
    proc = subprocess.run(  # noqa: S603 - fixed docker CLI, no shell
        ["docker", "image", "inspect", tag],  # noqa: S607 - docker resolved via PATH by design
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def ensure_image() -> str:
    """Return the agent image tag, building it first if this content revision is not present."""
    requirements = _locked_requirements()
    tag = f"{_IMAGE_PREFIX}:{_content_tag(requirements)}"
    if _image_exists(tag):
        return tag
    with tempfile.TemporaryDirectory(prefix="agent-sandbox-context-") as context:
        _stage_context(Path(context), requirements)
        proc = subprocess.run(  # noqa: S603 - fixed docker CLI, no shell
            ["docker", "build", "-f", str(_DOCKERFILE), "-t", tag, context],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    if proc.returncode != 0:
        raise ImageError((proc.stderr or proc.stdout).strip())
    return tag
