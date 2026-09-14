"""Tests for the assembled agent."""

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.native_tools import AbstractNativeTool

from agent_harness import HarnessSettings, agent, create_agent
from agent_harness.deps import HarnessDeps
from agent_harness.toolsets import ALL_TOOL_NAMES

EXPECTED_TOOLS = frozenset(
    {
        "bash",
        "python",
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
        "fetch",
        "web_extract",
        "think",
        "todo",
        "report",
        "ask_user",
        "confirm",
        "save_memory",
        "retrieve_memory",
        "list_memory_keys",
        "clear_memory",
    }
)


def test_agent_is_importable() -> None:
    assert agent is not None


def test_create_agent_applies_explicit_settings() -> None:
    configured = create_agent(
        HarnessSettings(model="openai:gpt-5", system_prompt="Custom policy", retries=7)
    )
    assert configured.model == "openai:gpt-5"
    assert configured._system_prompts == ("Custom policy",)
    assert configured._max_tool_retries == 7


def test_create_agent_loads_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_MODEL", "google:gemini-2.5-pro")
    assert create_agent().model == "google:gemini-2.5-pro"


def test_all_tools_registered() -> None:
    assert ALL_TOOL_NAMES == EXPECTED_TOOLS


def test_agent_runs_offline_on_test_model(deps: HarnessDeps) -> None:
    # Regression: a per-run model override must never trip provider-native
    # capabilities the model can't run (TestModel rejects all native tools).
    result = agent.run_sync("say hi", deps=deps, model=TestModel(call_tools=[]))
    assert isinstance(result.output, str)


def test_no_native_tools_requested(deps: HarnessDeps) -> None:
    seen: list[list[AbstractNativeTool]] = []

    def capture(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(list(info.model_request_parameters.native_tools))
        return ModelResponse(parts=[TextPart("done")])

    result = agent.run_sync("go", deps=deps, model=FunctionModel(capture))
    assert result.output == "done"
    assert seen == [[]]
