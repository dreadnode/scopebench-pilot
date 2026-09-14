"""Tests for the shared gate, snippet, and staleness helpers in ``_edit``."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry

from agent_harness._edit import (
    _NOTED_DELETED,
    check_current_read,
    render_snippet,
    require_read,
    staleness_notes,
)
from agent_harness.deps import HarnessDeps

Writer = Callable[[str, str], Path]


# ---------------------------------------------------------------- the gate


def test_gate_requires_a_read(deps: HarnessDeps, write_file: Writer) -> None:
    path = write_file("f.txt", "data")
    with pytest.raises(ModelRetry, match="has not been read yet"):
        check_current_read(deps, "f.txt", path, path.read_bytes())


def test_gate_names_the_action(deps: HarnessDeps, write_file: Writer) -> None:
    path = write_file("f.txt", "data")
    with pytest.raises(ModelRetry, match="before overwriting it"):
        check_current_read(deps, "f.txt", path, path.read_bytes(), "overwriting")


def test_gate_rejects_stale_digest(deps: HarnessDeps, seed_file: Writer) -> None:
    path = seed_file("f.txt", "old")
    _ = path.write_text("new")
    with pytest.raises(ModelRetry, match="has been modified since it was read"):
        check_current_read(deps, "f.txt", path, path.read_bytes())


def test_require_read_missing_file(deps: HarnessDeps) -> None:
    with pytest.raises(ModelRetry, match="File does not exist"):
        _ = require_read(deps, "nope.txt")


def test_require_read_returns_the_gated_bytes(deps: HarnessDeps, seed_file: Writer) -> None:
    path = seed_file("f.txt", "data")
    resolved, data = require_read(deps, "f.txt")
    assert resolved == path
    assert data == b"data"


# ---------------------------------------------------------------- render_snippet


def test_render_snippet_window() -> None:
    text = "\n".join(f"l{n}" for n in range(1, 13))
    lines = render_snippet(text, 6, 6).splitlines()
    assert lines[0] == "2\tl2"
    assert lines[-1] == "10\tl10"
    assert "6\tl6" in lines


def test_render_snippet_clamps_to_file_bounds() -> None:
    assert render_snippet("a\nb\nc", 1, 3) == "1\ta\n2\tb\n3\tc"


def test_render_snippet_past_end_is_clamped() -> None:
    assert render_snippet("a\nb", 9, 9) == "1\ta\n2\tb"


def test_render_snippet_empty_file() -> None:
    assert render_snippet("", 1, 1) == "(the file is now empty)"


# ---------------------------------------------------------------- staleness_notes


def test_staleness_note_fires_once_per_digest(deps: HarnessDeps, seed_file: Writer) -> None:
    path = seed_file("f.txt", "v1")
    _ = path.write_text("v2")
    expected = f"[note: {path} changed on disk during this command; read it again before editing.]"
    assert staleness_notes(deps) == [expected]
    assert staleness_notes(deps) == []


def test_staleness_note_fires_again_for_new_content(deps: HarnessDeps, seed_file: Writer) -> None:
    path = seed_file("f.txt", "v1")
    _ = path.write_text("v2")
    assert len(staleness_notes(deps)) == 1
    _ = path.write_text("v3")
    assert len(staleness_notes(deps)) == 1


def test_staleness_note_change_back_is_silent(deps: HarnessDeps, seed_file: Writer) -> None:
    path = seed_file("f.txt", "v1")
    _ = path.write_text("v2")
    _ = staleness_notes(deps)
    _ = path.write_text("v1")
    assert staleness_notes(deps) == []


def test_staleness_note_deleted_once(deps: HarnessDeps, seed_file: Writer) -> None:
    path = seed_file("f.txt", "v1")
    path.unlink()
    assert staleness_notes(deps) == [f"[note: {path} was deleted during this command.]"]
    assert deps.read_files[path].noted == _NOTED_DELETED
    assert staleness_notes(deps) == []


def test_staleness_notes_silent_when_fresh(deps: HarnessDeps, seed_file: Writer) -> None:
    _ = seed_file("f.txt", "v1")
    assert staleness_notes(deps) == []


def test_staleness_notes_skip_unreadable(
    deps: HarnessDeps, seed_file: Writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = seed_file("f.txt", "v1")

    def boom(_self: Path) -> bytes:
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_bytes", boom)
    assert staleness_notes(deps) == []


def test_staleness_deletion_beyond_cap_counted_then_named(
    deps: HarnessDeps, seed_file: Writer
) -> None:
    changed = [seed_file(f"c{n}.txt", "v1") for n in range(5)]
    for path in changed:
        _ = path.write_text("v2")
    deleted = seed_file("z.txt", "v1")  # sorts after the changed files
    deleted.unlink()
    notes = staleness_notes(deps)
    assert len(notes) == 6  # 5 named changes + the overflow counter
    assert "was deleted" not in "\n".join(notes)
    second = staleness_notes(deps)
    assert second == [f"[note: {deleted} was deleted during this command.]"]


def test_staleness_notes_cap_and_reannounce(deps: HarnessDeps, seed_file: Writer) -> None:
    paths = [seed_file(f"f{n}.txt", "v1") for n in range(7)]
    for path in paths:
        _ = path.write_text("v2")
    notes = staleness_notes(deps)
    assert len(notes) == 6  # 5 named + 1 overflow counter
    assert notes[-1] == "[note: 2 more tracked file(s) changed on disk during this command.]"
    second = staleness_notes(deps)
    assert len(second) == 2  # the overflow files are named on the next scan
    assert all("changed on disk during this command" in note for note in second)
    assert staleness_notes(deps) == []
