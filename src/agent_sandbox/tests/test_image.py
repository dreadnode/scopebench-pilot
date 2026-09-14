"""Tests for the agent-image build/reuse logic.

The docker CLI is mocked throughout. ``uv export`` is mocked in the ensure_image tests, but is
also driven for real (offline, against the checked-in lockfile) in the pin-export test — that is
the guard that the export flags stay valid and the pins actually come out of ``uv.lock``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_sandbox import ImageError, ensure_image, image

_PINS = "pydantic-ai==2.2.0\n"


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_locked_requirements_exports_pins_from_the_lockfile() -> None:
    requirements = image._locked_requirements()
    # Exact pins with hashes for the agent stack, so the in-image install is version- and
    # hash-checked against uv.lock.
    assert "pydantic-ai==" in requirements
    assert "pydantic==" in requirements
    assert "httpx==" in requirements
    assert "--hash=sha256:" in requirements
    # --no-header/--no-annotate: requirement lines only, and no dev/bench-only or project lines.
    assert not requirements.startswith("#")
    assert "pytest==" not in requirements
    assert "ruff==" not in requirements


def test_content_tag_is_deterministic_and_pin_sensitive() -> None:
    tag = image._content_tag(_PINS)
    assert tag == image._content_tag(_PINS)
    assert len(tag) == 12
    # A moved lockfile (different pins) must retag, or a cached image would keep the old deps.
    assert tag != image._content_tag("pydantic-ai==2.3.0\n")


def test_ensure_image_reuses_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        ran.append(cmd)
        if cmd[0] == "uv":
            return _completed(0, stdout=_PINS)
        return _completed(0)  # `docker image inspect` succeeds -> already present

    monkeypatch.setattr(subprocess, "run", fake)
    tag = ensure_image()
    assert tag.startswith("agent-sandbox:")
    assert all("build" not in cmd for cmd in ran)  # never builds when the image exists


def test_ensure_image_builds_a_staged_context_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    staged: dict[str, object] = {}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "uv":
            return _completed(0, stdout=_PINS)
        if "inspect" in cmd:
            return _completed(1)  # not present -> forces a build
        # The build context (last argv) must be a staged scratch dir, inspected here because it
        # is gone by the time ensure_image returns.
        context = Path(cmd[-1])
        staged["is_scratch"] = context.resolve() != image._REPO_ROOT
        staged["requirements"] = (context / "requirements.txt").read_text(encoding="utf-8")
        staged["library"] = (context / "src" / "agent_harness" / "__init__.py").is_file()
        staged["scopejudge"] = (context / "src" / "scopejudge" / "__main__.py").is_file()
        staged["tests"] = sorted(context.rglob("tests"))
        staged["pycache"] = sorted(context.rglob("__pycache__")) + sorted(context.rglob("*.pyc"))
        staged["extras"] = sorted(entry.name for entry in context.iterdir() if entry.name != "src")
        return _completed(0)

    monkeypatch.setattr(subprocess, "run", fake)
    tag = ensure_image()
    assert tag.startswith("agent-sandbox:")
    assert staged["is_scratch"] is True
    assert staged["requirements"] == _PINS  # the exported pins are what the Dockerfile installs
    assert staged["library"] is True
    assert staged["scopejudge"] is True
    assert staged["tests"] == []  # unit tests are not part of the executable image
    assert staged["pycache"] == []  # host bytecode never ships
    assert staged["extras"] == ["requirements.txt"]  # nothing beyond the declared source tree


def test_ensure_image_raises_on_build_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "uv":
            return _completed(0, stdout=_PINS)
        if "inspect" in cmd:
            return _completed(1)
        return _completed(1, stderr="build boom")

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(ImageError, match="build boom"):
        ensure_image()


def test_ensure_image_raises_when_the_pin_export_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd[0] == "uv"  # fails before any docker call
        return _completed(1, stderr="lockfile out of date")

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(ImageError, match=r"pinned dependencies.*lockfile out of date"):
        ensure_image()
