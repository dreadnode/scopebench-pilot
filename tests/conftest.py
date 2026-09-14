"""Shared pytest fixtures for the agent-harness test suite."""

from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import settings
from pydantic_ai import RunContext, RunUsage
from pydantic_ai.models.test import TestModel

from agent_harness.deps import FileRead, HarnessDeps, content_digest

# Property tests: the functions under test are micro-fast, so a per-example
# deadline only adds timing flakiness to the hard 100% gate.
settings.register_profile("agent-harness", deadline=None)
settings.load_profile("agent-harness")


def make_ctx(deps: HarnessDeps) -> RunContext[HarnessDeps]:
    """Build a real, fully-typed RunContext around ``deps`` for direct tool calls."""
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


@pytest.fixture
def deps(tmp_path: Path) -> HarnessDeps:
    """A HarnessDeps rooted in a temp dir, with reports written under it too."""
    return HarnessDeps(cwd=tmp_path, reports_dir=tmp_path / "reports")


@pytest.fixture
def ctx(deps: HarnessDeps) -> RunContext[HarnessDeps]:
    """A RunContext wrapping the default temp-dir ``deps``."""
    return make_ctx(deps)


@pytest.fixture
def make_context() -> Callable[[HarnessDeps], RunContext[HarnessDeps]]:
    """Factory for building a RunContext from custom deps within a test."""
    return make_ctx


@pytest.fixture
def write_file(deps: HarnessDeps) -> Callable[[str, str], Path]:
    """Factory that writes a file (creating parents) under the deps cwd."""

    def _write(name: str, content: str) -> Path:
        path = deps.cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(content)
        # Return the canonical path so tests that seed the read-gate (read_files) match what
        # the tools check via resolve_path (which canonicalizes).
        return path.resolve()

    return _write


@pytest.fixture
def mark_read(deps: HarnessDeps) -> Callable[[Path], None]:
    """Factory that records an existing file as read at its current bytes."""

    def _mark(path: Path) -> None:
        deps.read_files[path.resolve()] = FileRead(digest=content_digest(path.read_bytes()))

    return _mark


@pytest.fixture
def seed_file(
    write_file: Callable[[str, str], Path], mark_read: Callable[[Path], None]
) -> Callable[[str, str], Path]:
    """Factory that writes a file and marks it read, satisfying the edit gate."""

    def _seed(name: str, content: str) -> Path:
        path = write_file(name, content)
        mark_read(path)
        return path

    return _seed
