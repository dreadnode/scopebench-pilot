"""Tests for the toolset grouping and the approval predicate."""

from collections.abc import Callable

from pydantic_ai import RunContext, ToolDefinition

from agent_harness.deps import HarnessDeps
from agent_harness.toolsets import (
    ALL_TOOL_NAMES,
    TOOLSETS,
    _needs_approval,
    all_tool_definitions,
    execution,
    filesystem,
    memory,
    web,
    workflow,
)

MakeCtx = Callable[[HarnessDeps], RunContext[HarnessDeps]]


def test_execution_tools() -> None:
    assert set(execution.tools) == {"bash", "python"}


def test_filesystem_tools() -> None:
    assert set(filesystem.tools) == {
        "read",
        "ls",
        "glob",
        "grep",
        "write",
        "edit_file",
        "multiedit",
        "insert_lines",
        "delete_lines",
        "apply_patch",
    }


def test_web_tools() -> None:
    assert set(web.tools) == {"fetch", "web_extract"}


def test_workflow_tools() -> None:
    assert set(workflow.tools) == {"think", "todo", "report", "ask_user", "confirm"}


def test_memory_tools() -> None:
    assert set(memory.tools) == {
        "save_memory",
        "retrieve_memory",
        "list_memory_keys",
        "clear_memory",
    }


def test_five_toolsets() -> None:
    assert len(TOOLSETS) == 5


def test_filesystem_retry_headroom() -> None:
    # The read gate refuses via ModelRetry; the file tools get more consecutive-failure
    # headroom than the agent-wide default so gate refusals cannot kill a run cheaply.
    assert filesystem.max_retries == 4


def test_all_tool_names_count() -> None:
    assert len(ALL_TOOL_NAMES) == 23


def test_all_tool_definitions_cover_every_tool() -> None:
    definitions = all_tool_definitions()
    assert {td.name for td in definitions} == ALL_TOOL_NAMES
    assert len(definitions) == len(ALL_TOOL_NAMES)  # no duplicate names
    assert definitions == all_tool_definitions()  # deterministic order
    assert all(td.description for td in definitions)  # every harness tool is documented


def test_needs_approval_gates_destructive(make_context: MakeCtx, deps: HarnessDeps) -> None:
    deps.require_approval = True
    ctx = make_context(deps)
    td = ToolDefinition(name="write", metadata={"destructive": True})
    assert _needs_approval(ctx, td, {})


def test_needs_approval_skips_non_destructive(make_context: MakeCtx, deps: HarnessDeps) -> None:
    deps.require_approval = True
    ctx = make_context(deps)
    td = ToolDefinition(name="read", metadata=None)
    assert not _needs_approval(ctx, td, {})


def test_needs_approval_off_by_default(ctx: RunContext[HarnessDeps]) -> None:
    td = ToolDefinition(name="write", metadata={"destructive": True})
    assert not _needs_approval(ctx, td, {})
