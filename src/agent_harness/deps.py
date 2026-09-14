"""Runtime dependencies and shared types for the agent-harness agent tools.

The agent object itself is stateless. All per-run state (the working directory,
the in-memory key-value store, the todo list, and so on) lives in a
:class:`HarnessDeps` instance that is passed to ``agent.run``/``agent.run_sync``
and reaches every tool through ``RunContext.deps``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict

TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]
TodoPriority = Literal["high", "medium", "low"]
WebFormat = Literal["markdown", "text", "html"]


class TodoItem(TypedDict):
    """A single task in the session todo list."""

    id: str
    content: str
    status: TodoStatus
    priority: TodoPriority


class FetchResponse(TypedDict):
    """Structured result of fetching a single URL."""

    success: bool
    url: str
    final_url: str
    status_code: int
    content_type: str
    format: WebFormat
    title: str
    content_length: int
    truncated: bool
    content: str


class PageRecord(TypedDict):
    """Per-URL record returned by ``web_extract``."""

    success: bool
    url: str
    final_url: str
    status_code: int
    content_type: str
    title: str
    content: str
    content_length: int
    truncated: bool
    error: str | None


class WebExtractResponse(TypedDict):
    """Structured result of extracting content from several URLs."""

    success: bool
    partial: bool
    requested_count: int
    extracted_count: int
    warnings: list[str]
    results: list[PageRecord]


def content_digest(data: bytes) -> str:
    """SHA-256 hex digest of ``data``: the identity the read gate is keyed to."""
    return hashlib.sha256(data).hexdigest()


@dataclass
class FileRead:
    """Freshness record for one file: the digest of the bytes the model last saw.

    A file may be edited or overwritten only while its bytes still match this
    digest; any external change (``bash``, ``python``, another process) makes
    the record stale, and the file must be read again before further edits.

    Attributes:
        digest: :func:`content_digest` of the file's raw bytes when the model
            last read or wrote it.
        noted: Digest (or the ``"deleted"`` sentinel) already announced by a
            post-command staleness note, so each external change is announced
            at most once.
    """

    digest: str
    noted: str | None = None


@dataclass
class HarnessDeps:
    """Per-run state shared by all harness tools via ``RunContext.deps``.

    Attributes:
        cwd: Directory that filesystem and execution tools operate relative to.
        memory: In-memory key-value store backing the ``*_memory`` tools.
        todos: The session todo list backing the ``todo`` tool.
        reports_dir: Directory where ``report`` persists artifacts.
        require_approval: When ``True``, destructive tools (shell/Python execution
            and file writes) require caller approval before running; the run
            surfaces a ``DeferredToolRequests`` for them. Default ``False``.
        read_files: Per-file freshness records (:class:`FileRead`) keyed by
            resolved path. The editing tools require a file to have been read
            at its current on-disk content before they will modify it.
    """

    cwd: Path
    memory: dict[str, str] = field(default_factory=dict)
    todos: list[TodoItem] = field(default_factory=list)
    reports_dir: Path = field(default_factory=lambda: Path.home() / ".agent_harness" / "reports")
    require_approval: bool = False
    read_files: dict[Path, FileRead] = field(default_factory=dict)


def default_deps() -> HarnessDeps:
    """Build a :class:`HarnessDeps` rooted at the current working directory."""
    return HarnessDeps(cwd=Path.cwd())
