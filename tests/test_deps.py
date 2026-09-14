"""Tests for HarnessDeps, default_deps, and the FileRead freshness record."""

import hashlib
from pathlib import Path

from agent_harness.deps import FileRead, HarnessDeps, content_digest, default_deps


def test_dataclass_defaults(tmp_path: Path) -> None:
    d = HarnessDeps(cwd=tmp_path)
    assert d.memory == {}
    assert d.todos == []
    assert d.require_approval is False
    assert d.read_files == {}
    assert d.reports_dir.name == "reports"


def test_default_deps_uses_cwd() -> None:
    d = default_deps()
    assert d.cwd == Path.cwd()


def test_content_digest_is_sha256_hex() -> None:
    digest = content_digest(b"a\nb\nc\n")
    assert digest == hashlib.sha256(b"a\nb\nc\n").hexdigest()
    assert len(digest) == 64


def test_fileread_defaults() -> None:
    record = FileRead(digest=content_digest(b"x"))
    assert record.noted is None
