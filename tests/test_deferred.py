"""Integration tests for the deferred-approval flow through the real toolsets.

A scripted FunctionModel drives the toolsets (which carry the approval wiring).
"""

from pathlib import Path

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ModelMessage, ToolDenied
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent_harness.deps import HarnessDeps
from agent_harness.toolsets import TOOLSETS

ToolCall = tuple[str, dict[str, object], str]
DeferredAgent = Agent[HarnessDeps, "str | DeferredToolRequests"]


def _agent(*tool_calls: ToolCall) -> DeferredAgent:
    """A test agent that emits the given tool calls, then the text 'done'."""
    state = {"step": 0}

    def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        state["step"] += 1
        if state["step"] == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=n, args=a, tool_call_id=i) for n, a, i in tool_calls]
            )
        return ModelResponse(parts=[TextPart(content="done")])

    return Agent(
        FunctionModel(model),
        deps_type=HarnessDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=TOOLSETS,
    )


def test_confirm_and_ask_user_round_trip(tmp_path: Path) -> None:
    agent = _agent(
        ("confirm", {"action": "ship it"}, "c1"),
        ("ask_user", {"question": "name?"}, "a1"),
    )
    deps = HarnessDeps(cwd=tmp_path)
    r1 = agent.run_sync("go", deps=deps)
    assert isinstance(r1.output, DeferredToolRequests)
    assert {p.tool_name for p in r1.output.approvals} == {"confirm"}
    assert {p.tool_name for p in r1.output.calls} == {"ask_user"}
    results = DeferredToolResults(approvals={"c1": True}, calls={"a1": "Ada"})
    r2 = agent.run_sync(message_history=r1.all_messages(), deferred_tool_results=results, deps=deps)
    assert r2.output == "done"


def test_confirm_denied(tmp_path: Path) -> None:
    agent = _agent(("confirm", {"action": "rm -rf /"}, "c1"))
    deps = HarnessDeps(cwd=tmp_path)
    r1 = agent.run_sync("go", deps=deps)
    assert isinstance(r1.output, DeferredToolRequests)
    results = DeferredToolResults(approvals={"c1": ToolDenied("blocked")})
    r2 = agent.run_sync(message_history=r1.all_messages(), deferred_tool_results=results, deps=deps)
    assert r2.output == "done"


def test_destructive_gated_when_opted_in(tmp_path: Path) -> None:
    agent = _agent(("write", {"file_path": "f.txt", "content": "hi"}, "w1"))
    deps = HarnessDeps(cwd=tmp_path, require_approval=True)
    r1 = agent.run_sync("go", deps=deps)
    assert isinstance(r1.output, DeferredToolRequests)
    assert {p.tool_name for p in r1.output.approvals} == {"write"}
    results = DeferredToolResults(approvals={"w1": True})
    r2 = agent.run_sync(message_history=r1.all_messages(), deferred_tool_results=results, deps=deps)
    assert r2.output == "done"
    assert (tmp_path / "f.txt").read_text() == "hi"


def test_destructive_not_gated_by_default(tmp_path: Path) -> None:
    agent = _agent(("write", {"file_path": "f.txt", "content": "hi"}, "w1"))
    deps = HarnessDeps(cwd=tmp_path)
    r1 = agent.run_sync("go", deps=deps)
    assert r1.output == "done"
    assert (tmp_path / "f.txt").read_text() == "hi"
