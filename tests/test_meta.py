"""Meta tests pinning the project-config story the prose repeats.

The coverage/collection story has rotted in comments before (it used to claim only
``agent_harness`` was measured); these parse ``pyproject.toml`` and the READMEs directly,
so the recorded story and the enforced one cannot drift apart silently again.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from pydantic import TypeAdapter

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGES = {"agent_harness", "agent_sandbox", "scopebench", "scopejudge"}

# Shallow str-keyed views of parsed TOML, so nested access stays typed under
# basedpyright `all` (same idiom as scopebench.manifest's _MAPPING_ADAPTER).
_TABLE: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_STR_LIST: TypeAdapter[list[str]] = TypeAdapter(list[str])


def _section(*keys: str) -> dict[str, object]:
    node: object = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for key in keys:
        node = _TABLE.validate_python(node)[key]
    return _TABLE.validate_python(node)


def test_coverage_source_spans_all_four_packages() -> None:
    run = _section("tool", "coverage", "run")
    assert set(_STR_LIST.validate_python(run["source"])) == _PACKAGES
    assert run["branch"] is True
    assert "*/tests/*" in _STR_LIST.validate_python(run["omit"])  # test files unmeasured


def test_addopts_measures_and_gates_all_four_packages() -> None:
    addopts = _section("tool", "pytest", "ini_options")["addopts"]
    assert isinstance(addopts, str)
    for package in sorted(_PACKAGES):
        assert f"--cov={package}" in addopts
    assert "--cov-branch" in addopts
    assert "--cov-fail-under=100" in addopts
    # The 100% threshold lives ONLY in addopts: [tool.coverage.report] has no fail_under,
    # so `--no-cov` / partial runs never fight a second copy of the gate.
    assert "fail_under" not in _section("tool", "coverage", "report")


def test_testpaths_collect_all_four_suites() -> None:
    ini = _section("tool", "pytest", "ini_options")
    testpaths = set(_STR_LIST.validate_python(ini["testpaths"]))
    assert testpaths == {
        "tests",
        "src/scopebench/tests",
        "src/agent_sandbox/tests",
        "src/scopejudge/tests",
    }


def test_distribution_installs_scopebench_command_and_all_packages() -> None:
    project = _section("project")
    scripts = _TABLE.validate_python(project["scripts"])
    assert scripts["scopebench"] == "scopebench.__main__:main"
    build = _section("tool", "uv", "build-backend")
    assert set(_STR_LIST.validate_python(build["module-name"])) == _PACKAGES


# Markdown links only (`](target)`): backtick code spans may legitimately name external paths and
# must not fail this.
_MD_LINK = re.compile(r"\]\(([^)\s]+)\)")


def test_readme_relative_links_resolve() -> None:
    readme = "README.md"
    path = _ROOT / readme
    for target in _STR_LIST.validate_python(_MD_LINK.findall(path.read_text(encoding="utf-8"))):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        relative = target.partition("#")[0]
        if not relative:  # pure in-page anchor, e.g. (#grading)
            continue
        assert (path.parent / relative).exists(), f"{readme}: broken link {target!r}"
