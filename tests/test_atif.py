"""Tests for the ATIF v1.7 trajectory models and the message-history converter.

The converter is exercised against hand-built PydanticAI message histories so every part
kind, argument form, and observation-attachment path is covered; the vendored models are
exercised directly for their validators. Emitted trajectories are round-tripped through
``model_validate`` to prove they are self-consistent ATIF.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import (
    BinaryContent,
    CompactionPart,
    FilePart,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from agent_harness.atif import (
    AgentInfo,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
    trajectory_from_messages,
)

_TS = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)


def _convert(
    messages: Sequence[ModelMessage],
    *,
    session_id: str | None = None,
    model_name: str | None = None,
    trajectory_id: str | None = None,
    tool_definitions: Sequence[ToolDefinition] | None = None,
) -> Trajectory:
    """Convert with the fixed agent identity most tests share."""
    return trajectory_from_messages(
        messages,
        agent_name="agent-harness",
        agent_version="0.1.0",
        session_id=session_id,
        model_name=model_name,
        trajectory_id=trajectory_id,
        tool_definitions=tool_definitions,
    )


def _response(*parts: object, usage: RequestUsage | None = None, model: str = "m") -> ModelResponse:
    """Build a model response with a timestamp and optional usage."""
    return ModelResponse(
        parts=list(parts),  # pyright: ignore[reportArgumentType]
        usage=usage or RequestUsage(),
        model_name=model,
        timestamp=_TS,
    )


# --- basic conversion ------------------------------------------------------


def test_basic_three_step_conversion() -> None:
    messages = [
        ModelRequest(
            parts=[
                SystemPromptPart(content="You are a harness.", timestamp=_TS),
                UserPromptPart(content="do the thing", timestamp=_TS),
            ]
        ),
        _response(TextPart(content="all done")),
    ]
    traj = _convert(messages, session_id="run-1")

    assert traj.schema_version == "ATIF-v1.7"
    assert traj.session_id == "run-1"
    assert [s.step_id for s in traj.steps] == [1, 2, 3]
    assert [s.source for s in traj.steps] == ["system", "user", "agent"]
    assert traj.steps[0].message == "You are a harness."
    assert traj.steps[2].message == "all done"
    assert traj.steps[2].timestamp == _TS.isoformat()
    assert traj.agent.model_name == "m"  # derived from the only response
    assert traj.final_metrics is not None
    assert traj.final_metrics.total_steps == 3
    assert traj.final_metrics.extra is None  # no cache writes anywhere in this run


def test_multiple_text_parts_join_and_thinking() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi", timestamp=_TS)]),
        _response(
            ThinkingPart(content="step one"),
            ThinkingPart(content="step two"),
            TextPart(content="part A"),
            TextPart(content="part B"),
        ),
    ]
    agent_step = _convert(messages).steps[-1]
    assert agent_step.message == "part A\n\npart B"
    assert agent_step.reasoning_content == "step one\n\nstep two"


def test_empty_text_and_absent_thinking() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi", timestamp=_TS)]),
        _response(ToolCallPart(tool_name="bash", args={"cmd": "ls"}, tool_call_id="c1")),
    ]
    agent_step = _convert(messages).steps[-1]
    assert agent_step.message == ""  # no TextPart
    assert agent_step.reasoning_content is None  # no ThinkingPart


# --- user content flattening ----------------------------------------------


def test_user_content_sequence_placeholders_non_text() -> None:
    messages = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=["look at this", ImageUrl(url="https://example.com/x.png")],
                    timestamp=_TS,
                )
            ]
        ),
        _response(TextPart(content="ok")),
    ]
    user_step = _convert(messages).steps[0]
    assert user_step.message == "look at this\n\n[ImageUrl]"


# --- tool calls and observations ------------------------------------------


def test_tool_call_with_matched_return() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="go", timestamp=_TS)]),
        _response(ToolCallPart(tool_name="bash", args={"cmd": "ls"}, tool_call_id="c1")),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="bash", content="out", tool_call_id="c1", timestamp=_TS)
            ]
        ),
    ]
    agent_step = _convert(messages).steps[-1]
    assert agent_step.tool_calls is not None
    assert agent_step.tool_calls[0] == ToolCall(
        tool_call_id="c1", function_name="bash", arguments={"cmd": "ls"}
    )
    assert agent_step.observation is not None
    assert agent_step.observation.results[0].source_call_id == "c1"
    assert agent_step.observation.results[0].content == "out"


def test_mismatched_return_id_and_second_return_appends() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="go", timestamp=_TS)]),
        _response(ToolCallPart(tool_name="bash", args={}, tool_call_id="c1")),
        ModelRequest(
            parts=[
                # id "zzz" matches no call in the step -> source_call_id None (create observation)
                ToolReturnPart(tool_name="bash", content="one", tool_call_id="zzz", timestamp=_TS),
                # a second return -> appends to the same observation
                ToolReturnPart(tool_name="bash", content="two", tool_call_id="c1", timestamp=_TS),
            ]
        ),
    ]
    obs = _convert(messages).steps[-1].observation
    assert obs is not None
    assert [(r.source_call_id, r.content) for r in obs.results] == [(None, "one"), ("c1", "two")]


def test_retry_prompt_matched_and_unmatched() -> None:
    matched = [
        ModelRequest(parts=[UserPromptPart(content="go", timestamp=_TS)]),
        _response(ToolCallPart(tool_name="bash", args={}, tool_call_id="c1")),
        ModelRequest(
            parts=[RetryPromptPart(content="bad args", tool_name="bash", tool_call_id="c1")]
        ),
    ]
    obs = _convert(matched).steps[-1].observation
    assert obs is not None
    assert obs.results[0].source_call_id == "c1"
    assert "bad args" in (obs.results[0].content or "")

    # A text-only agent step (tool_calls is None) followed by an output-validation retry
    # whose tool_call_id matches nothing -> source_call_id None, exercising the None-calls path.
    unmatched = [
        ModelRequest(parts=[UserPromptPart(content="go", timestamp=_TS)]),
        _response(TextPart(content="here")),
        ModelRequest(parts=[RetryPromptPart(content="invalid", tool_name=None, tool_call_id="r1")]),
    ]
    obs2 = _convert(unmatched).steps[-1].observation
    assert obs2 is not None
    assert obs2.results[0].source_call_id is None


def test_orphan_return_is_dropped() -> None:
    # A tool return with no preceding agent step is dropped; the user step still stands.
    messages = [
        ModelRequest(
            parts=[
                UserPromptPart(content="hi", timestamp=_TS),
                ToolReturnPart(tool_name="x", content="orphan", tool_call_id="z1", timestamp=_TS),
            ]
        ),
    ]
    traj = _convert(messages)
    assert [s.source for s in traj.steps] == ["user"]
    assert traj.steps[0].observation is None


def test_builtin_tool_pair_attaches_same_step_observation() -> None:
    # Provider-executed search: call and return both live inside one ModelResponse.
    messages = [
        ModelRequest(parts=[UserPromptPart(content="search", timestamp=_TS)]),
        _response(
            NativeToolCallPart(tool_name="web_search", args={"q": "x"}, tool_call_id="b1"),
            NativeToolReturnPart(tool_name="web_search", content="hits", tool_call_id="b1"),
            TextPart(content="summary"),
        ),
    ]
    agent_step = _convert(messages).steps[-1]
    assert agent_step.tool_calls is not None
    assert agent_step.tool_calls[0].function_name == "web_search"
    assert agent_step.observation is not None
    assert agent_step.observation.results[0].source_call_id == "b1"
    assert agent_step.observation.results[0].content == "hits"


def test_compaction_emits_system_boundary_step() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi", timestamp=_TS)]),
        _response(
            CompactionPart(content="summary"),
            TextPart(content="kept"),
            ToolCallPart(tool_name="bash", args={}, tool_call_id="c1"),
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="bash", content="out", tool_call_id="c1", timestamp=_TS)
            ]
        ),
    ]
    traj = _convert(messages)
    assert [s.source for s in traj.steps] == ["user", "system", "agent"]
    assert [s.step_id for s in traj.steps] == [1, 2, 3]
    boundary, agent_step = traj.steps[1], traj.steps[2]
    assert boundary.message == "Context compaction performed"
    assert boundary.extra == {"context_management": {"type": "compaction", "boundary": "replace"}}
    assert boundary.timestamp == _TS.isoformat()
    assert boundary.observation is not None
    assert [(r.source_call_id, r.content) for r in boundary.observation.results] == [
        (None, "summary")
    ]
    assert agent_step.message == "kept"
    # The later tool return attaches to the agent step, not the boundary.
    assert agent_step.observation is not None
    assert agent_step.observation.results[0].source_call_id == "c1"


def test_contentless_compaction_and_file_parts_are_skipped() -> None:
    # An encrypted (non-Anthropic) compaction has no summary text and FilePart has no ATIF
    # representation: neither yields a step or observation.
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi", timestamp=_TS)]),
        _response(
            CompactionPart(content=None),
            FilePart(content=BinaryContent(data=b"x", media_type="image/png")),
            TextPart(content="kept"),
        ),
    ]
    traj = _convert(messages)
    assert [s.source for s in traj.steps] == ["user", "agent"]
    assert traj.steps[-1].message == "kept"
    assert traj.steps[-1].observation is None


def test_multiple_compaction_parts_share_one_boundary() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi", timestamp=_TS)]),
        _response(
            CompactionPart(content="s1"), CompactionPart(content="s2"), TextPart(content="ok")
        ),
    ]
    boundary = _convert(messages).steps[1]
    assert boundary.source == "system"
    assert boundary.observation is not None
    assert [r.content for r in boundary.observation.results] == ["s1", "s2"]


def test_compaction_only_response_still_emits_agent_step() -> None:
    # The agent step carries the call's metrics even when compaction was its only content.
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi", timestamp=_TS)]),
        _response(
            CompactionPart(content="summary"),
            usage=RequestUsage(input_tokens=7, output_tokens=2),
        ),
    ]
    traj = _convert(messages)
    assert [s.source for s in traj.steps] == ["user", "system", "agent"]
    agent_step = traj.steps[-1]
    assert agent_step.message == ""
    assert agent_step.llm_call_count == 1
    assert agent_step.metrics is not None
    assert agent_step.metrics.prompt_tokens == 7


# --- argument forms --------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (None, {}),
        ({"k": "v"}, {"k": "v"}),
        ('{"k": "v"}', {"k": "v"}),
        ("not json", {"INVALID_JSON": "not json"}),
    ],
)
def test_tool_call_argument_forms(
    args: str | dict[str, object] | None, expected: dict[str, object]
) -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="go", timestamp=_TS)]),
        _response(ToolCallPart(tool_name="bash", args=args, tool_call_id="c1")),
    ]
    calls = _convert(messages).steps[-1].tool_calls
    assert calls is not None
    assert calls[0].arguments == expected


# --- instructions dedup ----------------------------------------------------


def test_instructions_emit_dedup_and_change() -> None:
    messages = [
        ModelRequest(instructions="sys A", parts=[UserPromptPart(content="one", timestamp=_TS)]),
        _response(TextPart(content="r1")),
        # Same instructions replayed -> no new system step.
        ModelRequest(instructions="sys A", parts=[UserPromptPart(content="two", timestamp=_TS)]),
        _response(TextPart(content="r2")),
        # Changed instructions -> a fresh system step.
        ModelRequest(instructions="sys B", parts=[UserPromptPart(content="three", timestamp=_TS)]),
        _response(TextPart(content="r3")),
    ]
    traj = _convert(messages)
    system_messages = [s.message for s in traj.steps if s.source == "system"]
    assert system_messages == ["sys A", "sys B"]
    # Instruction system steps carry the request timestamp, which is None here.
    assert traj.steps[0].timestamp is None


# --- token metrics ---------------------------------------------------------


def test_token_metrics_mapping_and_totals() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="go", timestamp=_TS)]),
        _response(
            TextPart(content="a"),
            usage=RequestUsage(
                input_tokens=100, cache_read_tokens=40, cache_write_tokens=10, output_tokens=5
            ),
        ),
        ModelRequest(parts=[UserPromptPart(content="again", timestamp=_TS)]),
        _response(TextPart(content="b"), usage=RequestUsage(input_tokens=50, output_tokens=3)),
    ]
    traj = _convert(messages)
    first = traj.steps[1].metrics
    assert first is not None
    # input_tokens already includes the cached subset; do not re-add cache tokens.
    assert (first.prompt_tokens, first.cached_tokens, first.completion_tokens) == (100, 40, 5)
    # Separately-billed cache writes surface in extra, not in prompt_tokens.
    assert first.extra == {"cache_creation_input_tokens": 10}
    second = traj.steps[3].metrics
    assert second is not None
    assert second.extra is None  # no cache writes on this call
    assert traj.final_metrics is not None
    assert traj.final_metrics.total_prompt_tokens == 150
    assert traj.final_metrics.total_completion_tokens == 8
    assert traj.final_metrics.total_cached_tokens == 40
    assert traj.final_metrics.extra == {"total_cache_creation_input_tokens": 10}


# --- agent identity fields --------------------------------------------------


def test_tool_definitions_render_openai_shape() -> None:
    schema = {"type": "object", "properties": {"cmd": {"type": "string"}}}
    defs = [
        ToolDefinition(name="bash", description="Run a command.", parameters_json_schema=schema),
        ToolDefinition(name="terse"),  # description=None -> the key is omitted, not null
    ]
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi", timestamp=_TS)]),
        _response(TextPart(content="ok")),
    ]
    traj = _convert(messages, tool_definitions=defs)
    assert traj.agent.tool_definitions == [
        {
            "type": "function",
            "function": {"name": "bash", "description": "Run a command.", "parameters": schema},
        },
        {
            "type": "function",
            "function": {"name": "terse", "parameters": {"type": "object", "properties": {}}},
        },
    ]
    assert _convert(messages).agent.tool_definitions is None


def test_trajectory_id_recorded() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi", timestamp=_TS)]),
        _response(TextPart(content="ok")),
    ]
    assert _convert(messages, trajectory_id="doc-1").trajectory_id == "doc-1"
    assert _convert(messages).trajectory_id is None


# --- model name derivation -------------------------------------------------


def test_model_name_param_overrides_derived() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi", timestamp=_TS)]),
        _response(TextPart(content="ok"), model="derived-model"),
    ]
    assert _convert(messages, model_name="override").agent.model_name == "override"


def test_model_name_none_when_no_responses() -> None:
    messages = [ModelRequest(parts=[UserPromptPart(content="hi", timestamp=_TS)])]
    traj = _convert(messages)
    assert traj.agent.model_name is None
    assert [s.source for s in traj.steps] == ["user"]


def test_empty_messages_raises() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        _convert([])


# --- serialization round-trip ---------------------------------------------


def test_round_trip_and_no_null_keys() -> None:
    messages = [
        ModelRequest(
            parts=[
                SystemPromptPart(content="sys", timestamp=_TS),
                UserPromptPart(content="go", timestamp=_TS),
            ]
        ),
        _response(
            ToolCallPart(tool_name="bash", args={"cmd": "ls"}, tool_call_id="c1"),
            usage=RequestUsage(input_tokens=20, cache_write_tokens=6, output_tokens=2),
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="bash", content="out", tool_call_id="c1", timestamp=_TS)
            ]
        ),
        _response(CompactionPart(content="everything so far"), TextPart(content="done")),
    ]
    traj = _convert(
        messages,
        session_id="run-1",
        trajectory_id="doc-1",
        tool_definitions=[ToolDefinition(name="bash")],  # no description -> key omitted
    )
    dumped = traj.model_dump_json(exclude_none=True, indent=2)
    payload = cast("dict[str, object]", json.loads(dumped))

    # exclude_none must leave no null values anywhere in the tree.
    assert _has_no_nulls(payload)
    # Round-trips back into a valid Trajectory.
    restored = Trajectory.model_validate(payload)
    assert restored == traj


def _has_no_nulls(value: object) -> bool:
    match value:
        case dict():
            items = cast("dict[str, object]", value)
            return all(v is not None and _has_no_nulls(v) for v in items.values())
        case list():
            return all(_has_no_nulls(v) for v in cast("list[object]", value))
        case _:
            return True


# --- vendored model validators --------------------------------------------


def _agent_step(step_id: int = 1, **kwargs: object) -> Step:
    return Step(step_id=step_id, source="agent", message="m", **kwargs)  # pyright: ignore[reportArgumentType]


def test_step_rejects_bad_timestamp() -> None:
    with pytest.raises(ValidationError):
        Step(step_id=1, source="system", message="m", timestamp="not-a-time")


def test_step_allows_none_and_valid_timestamp() -> None:
    assert Step(step_id=1, source="system", message="m", timestamp=None).timestamp is None
    assert Step(step_id=1, source="system", message="m", timestamp="2026-07-14T00:00:00Z")


def test_step_forbids_agent_only_fields_on_non_agent() -> None:
    with pytest.raises(ValidationError, match="only allowed when source is 'agent'"):
        Step(step_id=1, source="user", message="m", tool_calls=[])


def test_step_allows_agent_only_fields_on_agent() -> None:
    step = _agent_step(reasoning_content="thinking", llm_call_count=1)
    assert step.reasoning_content == "thinking"


def test_step_zero_llm_calls_forbids_inference_fields() -> None:
    with pytest.raises(ValidationError, match="must be absent when llm_call_count is 0"):
        _agent_step(llm_call_count=0, metrics=Metrics(prompt_tokens=1))
    with pytest.raises(ValidationError, match="must be absent when llm_call_count is 0"):
        _agent_step(llm_call_count=0, reasoning_content="thought")


def test_step_zero_llm_calls_allows_deterministic_dispatch() -> None:
    assert _agent_step(llm_call_count=0).llm_call_count == 0
    # Non-agent steps are unaffected by the zero-call rule.
    assert Step(step_id=1, source="system", message="m", llm_call_count=0).llm_call_count == 0


def test_trajectory_requires_sequential_step_ids() -> None:
    steps = [_agent_step(step_id=1), _agent_step(step_id=3)]
    with pytest.raises(ValidationError, match="expected 2"):
        Trajectory(agent=_agent_info(), steps=steps)


def test_trajectory_rejects_cross_step_source_call_id() -> None:
    step = _agent_step(
        tool_calls=[ToolCall(tool_call_id="c1", function_name="bash", arguments={})],
        observation=Observation(results=[ObservationResult(source_call_id="other", content="x")]),
    )
    with pytest.raises(ValidationError, match="matches no tool call"):
        Trajectory(agent=_agent_info(), steps=[step])


def test_trajectory_allows_observation_without_source_call_id() -> None:
    step = _agent_step(
        observation=Observation(results=[ObservationResult(source_call_id=None, content="x")])
    )
    assert Trajectory(agent=_agent_info(), steps=[step]).steps[0].observation is not None


def test_trajectory_requires_at_least_one_step() -> None:
    with pytest.raises(ValidationError):
        Trajectory(agent=_agent_info(), steps=[])


def test_model_forbids_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        ToolCall.model_validate(
            {"tool_call_id": "c1", "function_name": "bash", "arguments": {}, "surprise": 1}
        )


def _agent_info() -> AgentInfo:
    return AgentInfo(name="agent-harness", version="0.1.0")
