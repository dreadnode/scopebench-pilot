"""Tests for the report tool."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from agent_harness.deps import HarnessDeps
from agent_harness.tools.report import report

Writer = Callable[[str, str], Path]


def test_content(ctx: RunContext[HarnessDeps]) -> None:
    msg = report(ctx, "hello", title="My Note")
    assert "Saved markdown report" in msg
    assert (ctx.deps.reports_dir / "my-note.md").read_text() == "hello"


def test_source_path_default_name(ctx: RunContext[HarnessDeps], write_file: Writer) -> None:
    write_file("src.md", "from file")
    report(ctx, source_path="src.md")
    assert (ctx.deps.reports_dir / "report.md").read_text() == "from file"


def test_source_path_missing(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry):
        report(ctx, source_path="nope.md")


def test_both_provided(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry):
        report(ctx, "x", source_path="y")


def test_neither_provided(ctx: RunContext[HarnessDeps]) -> None:
    with pytest.raises(ModelRetry):
        report(ctx)


def test_explicit_filename(ctx: RunContext[HarnessDeps]) -> None:
    report(ctx, "data", filename="custom.txt")
    assert (ctx.deps.reports_dir / "custom.txt").read_text() == "data"


@pytest.mark.parametrize("bad", ["../evil.sh", "sub/x.txt", "/etc/evil.sh", "..", ".", ""])
def test_rejects_path_in_filename(ctx: RunContext[HarnessDeps], bad: str) -> None:
    with pytest.raises(ModelRetry):
        report(ctx, "data", filename=bad)
    # Nothing escaped the reports dir (and the report was not written under a coerced name).
    assert not (ctx.deps.reports_dir / "evil.sh").exists()


def test_slug_fallback(ctx: RunContext[HarnessDeps]) -> None:
    report(ctx, "x", title="###")
    assert (ctx.deps.reports_dir / "report.md").exists()


def test_format_html(ctx: RunContext[HarnessDeps]) -> None:
    report(ctx, "<p>x</p>", title="Page", format="html")
    assert (ctx.deps.reports_dir / "page.html").exists()
