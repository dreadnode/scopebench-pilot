"""ATIF v1.7 trajectory models and a converter from PydanticAI message history.

ATIF (Agent Trajectory Interchange Format) is Harbor's portable JSON format for agent
runs (schema ``ATIF-v1.7``); emitting it lets a agent-harness run be inspected in Harbor's
trajectory viewer, scored by rewardkit, or fed into SFT pipelines. Rather than depend on
the whole ``harbor`` framework for ~a dozen model classes, this module vendors the subset
Harbor's reference models (``harbor.models.trajectories``) define, including their core
validators, so a :class:`Trajectory` built here is self-validating and byte-compatible
with Harbor's own validator.

Deliberately omitted from the vendored subset (agent-harness never produces them): Harbor's
multimodal ``ContentPart``/``ImageSource`` (``message`` and observation ``content`` are
typed as plain ``str``), the subagent embedding types
(``SubagentTrajectoryRef``/``subagent_trajectories``) — the harness is a single agent —
the unused ``notes``/``continued_trajectory_ref`` fields and root/tool-call/observation
``extra`` objects, and the token-id/logprob metrics (the Anthropic API exposes neither).

Token mapping (from PydanticAI's :class:`~pydantic_ai.usage.RequestUsage`): ``input_tokens``
is already the inclusive input total for Anthropic (cache reads and writes included), so it
maps straight to ATIF ``prompt_tokens``; ``cache_read_tokens`` is the cached subset and
``output_tokens`` the completion count. Summing the cache fields into ``prompt_tokens``
would double-count. Separately-billed cache writes land in ``metrics.extra`` as
``cache_creation_input_tokens``, the spot the spec reserves for provider-specific cost
factors. Mid-run compaction (a ``CompactionPart`` in a response) is emitted per the spec's
context-management convention: a ``system`` boundary step whose ``extra`` declares
``{"context_management": {"type": "compaction", "boundary": "replace"}}`` and whose
observation carries the summary that replaces all prior context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai.messages import (
    BaseToolCallPart,
    BaseToolReturnPart,
    CompactionPart,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    UserPromptPart,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from pydantic_ai.messages import ModelMessage, UserContent
    from pydantic_ai.tools import ToolDefinition
    from pydantic_ai.usage import RequestUsage

SCHEMA_VERSION = "ATIF-v1.7"
_EMPTY_MESSAGES_MSG = "ATIF requires at least one step; message history is empty"

# Step fields that ATIF only permits on ``source == "agent"`` steps.
_AGENT_ONLY_FIELDS = (
    "model_name",
    "reasoning_effort",
    "reasoning_content",
    "tool_calls",
    "metrics",
)


class _AtifModel(BaseModel):
    """Base for the vendored ATIF models: reject unknown keys so output stays spec-exact."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class ToolCall(_AtifModel):
    """A single tool invocation the agent made within an agent step."""

    tool_call_id: str = Field(description="Unique identifier for this tool call.")
    function_name: str = Field(description="Name of the tool/function invoked.")
    arguments: dict[str, object] = Field(description="Arguments passed to the tool (may be empty).")


class ObservationResult(_AtifModel):
    """One result inside an observation, optionally tied to a tool call."""

    source_call_id: str | None = Field(
        default=None,
        description="The tool_call_id this result answers, or None for non-tool results.",
    )
    content: str | None = Field(default=None, description="The tool output as the model saw it.")


class Observation(_AtifModel):
    """Environment feedback attached to the agent step that triggered it."""

    results: list[ObservationResult] = Field(description="Results from this step's tool calls.")


class Metrics(_AtifModel):
    """Per-step LLM token usage."""

    prompt_tokens: int | None = Field(default=None, description="Input tokens (cached + uncached).")
    completion_tokens: int | None = Field(default=None, description="Tokens the model generated.")
    cached_tokens: int | None = Field(
        default=None, description="Subset of prompt_tokens served from cache."
    )
    cost_usd: float | None = Field(default=None, description="Monetary cost of the call, if known.")
    extra: dict[str, object] | None = Field(
        default=None, description="Provider-specific metrics (e.g. cache_creation_input_tokens)."
    )


class FinalMetrics(_AtifModel):
    """Aggregate token usage for the whole trajectory."""

    total_prompt_tokens: int | None = Field(default=None, description="Sum of step prompt tokens.")
    total_completion_tokens: int | None = Field(
        default=None, description="Sum of step completion tokens."
    )
    total_cached_tokens: int | None = Field(default=None, description="Sum of step cached tokens.")
    total_cost_usd: float | None = Field(default=None, description="Total cost, if known.")
    total_steps: int | None = Field(default=None, ge=0, description="Number of steps.")
    extra: dict[str, object] | None = Field(
        default=None, description="Custom aggregate metrics not covered by the core fields."
    )


class AgentInfo(_AtifModel):
    """The agent that produced a trajectory (Harbor names this model ``Agent``).

    Renamed here to avoid shadowing :class:`pydantic_ai.Agent`.
    """

    name: str = Field(description="Name of the agent system.")
    version: str = Field(description="Version identifier of the agent system.")
    model_name: str | None = Field(default=None, description="Default model for the trajectory.")
    tool_definitions: list[dict[str, object]] | None = Field(
        default=None, description="Tools available to the agent, in OpenAI function-calling form."
    )


class Step(_AtifModel):
    """A single turn in the trajectory (a system/user message or an agent response)."""

    step_id: int = Field(ge=1, description="Ordinal index of the turn (sequential from 1).")
    timestamp: str | None = Field(default=None, description="ISO 8601 time the step occurred.")
    source: Literal["system", "user", "agent"] = Field(description="Originator of the step.")
    model_name: str | None = Field(default=None, description="Model used (agent steps only).")
    reasoning_effort: str | float | None = Field(
        default=None, description="Effort measure (agent steps only)."
    )
    message: str = Field(description="The dialogue message for this step.")
    reasoning_content: str | None = Field(
        default=None, description="The agent's internal reasoning (agent steps only)."
    )
    tool_calls: list[ToolCall] | None = Field(
        default=None, description="Tool calls the agent made (agent steps only)."
    )
    observation: Observation | None = Field(
        default=None, description="Environment feedback after the step's actions."
    )
    metrics: Metrics | None = Field(
        default=None, description="Token usage for the step (agent steps only)."
    )
    extra: dict[str, object] | None = Field(
        default=None, description="Custom step metadata (e.g. the context_management convention)."
    )
    is_copied_context: bool | None = Field(
        default=None, description="Whether the step was copied from a prior trajectory."
    )
    llm_call_count: int | None = Field(
        default=None, ge=0, description="Number of LLM inferences this step represents."
    )

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: str | None) -> str | None:
        """Reject a non-ISO-8601 timestamp string (``None`` is allowed)."""
        if value is not None:
            from datetime import datetime  # noqa: PLC0415 - only needed for this validation

            _ = datetime.fromisoformat(value)  # 3.13 parses a trailing 'Z'; raises on bad input
        return value

    @model_validator(mode="after")
    def _validate_agent_only_fields(self) -> Step:
        """Forbid agent-only fields on non-agent steps, per the ATIF spec."""
        if self.source != "agent":
            for name in _AGENT_ONLY_FIELDS:
                if getattr(self, name) is not None:
                    msg = f"field {name!r} is only allowed when source is 'agent'"
                    raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_deterministic_dispatch(self) -> Step:
        """Forbid inference-only fields on ``llm_call_count == 0`` agent steps, per the spec."""
        if self.llm_call_count == 0 and self.source == "agent":
            for name in ("metrics", "reasoning_content"):
                if getattr(self, name) is not None:
                    msg = f"field {name!r} must be absent when llm_call_count is 0"
                    raise ValueError(msg)
        return self


class Trajectory(_AtifModel):
    """A complete ATIF v1.7 agent trajectory."""

    schema_version: Literal["ATIF-v1.7"] = Field(
        default=SCHEMA_VERSION, description="ATIF schema version."
    )
    session_id: str | None = Field(default=None, description="Identifier for the agent run.")
    trajectory_id: str | None = Field(
        default=None, description="Unique identifier for this trajectory document."
    )
    agent: AgentInfo = Field(description="The agent that produced this trajectory.")
    steps: list[Step] = Field(min_length=1, description="The full interaction history.")
    final_metrics: FinalMetrics | None = Field(
        default=None, description="Aggregate metrics for the trajectory."
    )

    @model_validator(mode="after")
    def _validate_step_ids(self) -> Trajectory:
        """Require step ids to be sequential starting from 1."""
        for index, step in enumerate(self.steps):
            expected = index + 1
            if step.step_id != expected:
                msg = f"steps[{index}].step_id: expected {expected}, got {step.step_id}"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_tool_call_references(self) -> Trajectory:
        """Require each observation ``source_call_id`` to match a tool call in the same step."""
        for step in self.steps:
            if step.observation is None:
                continue
            ids = {call.tool_call_id for call in step.tool_calls} if step.tool_calls else set[str]()
            for result in step.observation.results:
                if result.source_call_id is not None and result.source_call_id not in ids:
                    msg = (
                        f"step {step.step_id}: observation source_call_id "
                        f"{result.source_call_id!r} matches no tool call"
                    )
                    raise ValueError(msg)
        return self


def _iso(timestamp: datetime | None) -> str | None:
    """Render a datetime as ISO 8601, passing ``None`` through."""
    return timestamp.isoformat() if timestamp is not None else None


def _user_content_str(content: str | Sequence[UserContent]) -> str:
    """Flatten user-prompt content to a string, placeholdering non-text parts."""
    if isinstance(content, str):
        return content
    parts = [item if isinstance(item, str) else f"[{type(item).__name__}]" for item in content]
    return "\n\n".join(parts)


def _tool_definitions(definitions: Sequence[ToolDefinition]) -> list[dict[str, object]]:
    """Render tool definitions in the OpenAI function-calling form the ATIF spec prescribes."""
    rendered: list[dict[str, object]] = []
    for definition in definitions:
        function: dict[str, object] = {"name": definition.name}
        if definition.description is not None:  # omit the key rather than emit a null
            function["description"] = definition.description
        function["parameters"] = definition.parameters_json_schema
        rendered.append({"type": "function", "function": function})
    return rendered


def _metrics(usage: RequestUsage) -> Metrics:
    """Map a PydanticAI per-response usage onto ATIF step metrics.

    Anthropic bills cache writes separately, so they go to ``extra`` per the ATIF spec; they
    are already counted inside ``input_tokens`` and must not be re-added to ``prompt_tokens``.
    """
    writes = usage.cache_write_tokens
    return Metrics(
        prompt_tokens=usage.input_tokens,
        completion_tokens=usage.output_tokens,
        cached_tokens=usage.cache_read_tokens,
        extra={"cache_creation_input_tokens": writes} if writes else None,
    )


def _final_metrics(messages: Sequence[ModelMessage], total_steps: int) -> FinalMetrics:
    """Sum token usage across every model response into trajectory-level totals."""
    prompt = completion = cached = writes = 0
    for message in messages:
        if isinstance(message, ModelResponse):
            prompt += message.usage.input_tokens
            completion += message.usage.output_tokens
            cached += message.usage.cache_read_tokens
            writes += message.usage.cache_write_tokens
    return FinalMetrics(
        total_prompt_tokens=prompt,
        total_completion_tokens=completion,
        total_cached_tokens=cached,
        total_steps=total_steps,
        extra={"total_cache_creation_input_tokens": writes} if writes else None,
    )


def _attach_observation(step: Step | None, tool_call_id: str, content: str) -> None:
    """Append an observation result to an agent ``step`` (a no-op when ``step`` is ``None``).

    ``source_call_id`` is kept only when it matches one of the step's tool calls; otherwise it
    is ``None`` (valid per ATIF — e.g. an output-validation retry has no matching call). A
    ``None`` step is an orphan return with no preceding agent step and is dropped; this cannot
    occur in a real run (returns always follow the response that made the calls).
    """
    if step is None:
        return
    ids = {call.tool_call_id for call in step.tool_calls} if step.tool_calls else set[str]()
    source_call_id = tool_call_id if tool_call_id in ids else None
    result = ObservationResult(source_call_id=source_call_id, content=content)
    if step.observation is None:
        step.observation = Observation(results=[result])
    else:
        step.observation.results.append(result)


def _compaction_step(summaries: list[str], step_id: int, timestamp: datetime | None) -> Step:
    """Build the system boundary step for a context compaction (ATIF context-management).

    ``boundary: "replace"`` declares that the observation's summaries stand in for all prior
    context from here on — matching Anthropic server-side compaction, where later requests
    round-trip the summary instead of the full history. Prior steps stay in the trajectory
    for auditability; consumers reconstructing context must start from this step.
    """
    results = [ObservationResult(source_call_id=None, content=summary) for summary in summaries]
    return Step(
        step_id=step_id,
        source="system",
        timestamp=_iso(timestamp),
        message="Context compaction performed",
        observation=Observation(results=results),
        extra={"context_management": {"type": "compaction", "boundary": "replace"}},
    )


def _agent_step(response: ModelResponse, step_id: int) -> list[Step]:
    """Convert one model response into ATIF steps, starting at ``step_id``.

    Returns the agent step alone, or — when the response carries a context compaction — a
    system boundary step (see :func:`_compaction_step`) followed by the agent step built
    from the response's remaining parts.
    """
    texts: list[str] = []
    thinking: list[str] = []
    calls: list[ToolCall] = []
    returns: list[tuple[str, str]] = []
    summaries: list[str] = []
    for part in response.parts:
        match part:
            case TextPart(content=content):
                texts.append(content)
            case ThinkingPart(content=content):
                thinking.append(content)
            case BaseToolCallPart() as tool_call:
                calls.append(
                    ToolCall(
                        tool_call_id=tool_call.tool_call_id,
                        function_name=tool_call.tool_name,
                        arguments=cast("dict[str, object]", tool_call.args_as_dict()),
                    )
                )
            case BaseToolReturnPart() as tool_return:
                # Provider-executed results (e.g. web search) arrive inline in the response.
                returns.append((tool_return.tool_call_id, tool_return.model_response_str()))
            case CompactionPart(content=content) if content:
                summaries.append(content)
            case _:
                # A contentless CompactionPart (non-Anthropic, encrypted server-side) and
                # FilePart carry no ATIF representation and are skipped.
                pass
    steps: list[Step] = []
    if summaries:
        steps.append(_compaction_step(summaries, step_id, response.timestamp))
    step = Step(
        step_id=step_id + len(steps),
        source="agent",
        timestamp=_iso(response.timestamp),
        model_name=response.model_name,
        message="\n\n".join(texts),
        reasoning_content="\n\n".join(thinking) if thinking else None,
        tool_calls=calls or None,
        metrics=_metrics(response.usage),
        llm_call_count=1,
    )
    for tool_call_id, content in returns:
        _attach_observation(step, tool_call_id, content)
    steps.append(step)
    return steps


def _handle_request(
    request: ModelRequest,
    steps: list[Step],
    last_agent: Step | None,
    seen_instructions: str | None,
) -> str | None:
    """Convert one model request: append system/user steps, attach observations to ``last_agent``.

    Returns the (possibly updated) most-recent instructions so a caller can suppress repeat
    system steps when resumed runs replay identical instructions.
    """
    instructions = request.instructions
    if instructions and instructions != seen_instructions:
        steps.append(
            Step(
                step_id=len(steps) + 1,
                source="system",
                message=instructions,
                timestamp=_iso(request.timestamp),
            )
        )
        seen_instructions = instructions
    for part in request.parts:
        match part:
            case SystemPromptPart(content=content, timestamp=timestamp):
                steps.append(
                    Step(
                        step_id=len(steps) + 1,
                        source="system",
                        message=content,
                        timestamp=_iso(timestamp),
                    )
                )
            case UserPromptPart(content=content, timestamp=timestamp):
                steps.append(
                    Step(
                        step_id=len(steps) + 1,
                        source="user",
                        message=_user_content_str(content),
                        timestamp=_iso(timestamp),
                    )
                )
            case BaseToolReturnPart() as tool_return:
                _attach_observation(
                    last_agent, tool_return.tool_call_id, tool_return.model_response_str()
                )
            case _:
                # RetryPromptPart: a validation-failure message rendered as the model saw it.
                _attach_observation(last_agent, part.tool_call_id, part.model_response())
    return seen_instructions


def trajectory_from_messages(
    messages: Sequence[ModelMessage],
    *,
    agent_name: str,
    agent_version: str,
    model_name: str | None = None,
    session_id: str | None = None,
    trajectory_id: str | None = None,
    tool_definitions: Sequence[ToolDefinition] | None = None,
) -> Trajectory:
    """Build an ATIF v1.7 :class:`Trajectory` from a PydanticAI message history.

    Each model response becomes one agent step (preceded by a system boundary step when the
    response carries a context compaction); system/user prompt parts become their own steps;
    tool returns and retry prompts attach as observations on the agent step that made the
    calls. The message history from :func:`pydantic_ai.capture_run_messages` or
    ``run.all_messages()`` is the complete conversation exactly once, so no de-duplication of
    replayed history is needed.

    Args:
        messages: The run's message history (must yield at least one step).
        agent_name: Name recorded on the trajectory's agent.
        agent_version: Version recorded on the trajectory's agent.
        model_name: Default model for the trajectory; falls back to the first response's model.
        session_id: Optional identifier for the agent run.
        trajectory_id: Optional unique identifier for this trajectory document.
        tool_definitions: The agent's tools, recorded in OpenAI function-calling form.

    Returns:
        A validated ATIF trajectory.

    Raises:
        ValueError: If ``messages`` produces no steps (ATIF requires at least one).
    """
    steps: list[Step] = []
    last_agent: Step | None = None
    seen_instructions: str | None = None
    derived_model: str | None = None
    for message in messages:
        match message:
            case ModelRequest():
                seen_instructions = _handle_request(message, steps, last_agent, seen_instructions)
            case _:
                new_steps = _agent_step(message, len(steps) + 1)
                steps.extend(new_steps)
                last_agent = new_steps[-1]
                if derived_model is None:
                    derived_model = message.model_name
    if not steps:
        raise ValueError(_EMPTY_MESSAGES_MSG)
    agent = AgentInfo(
        name=agent_name,
        version=agent_version,
        model_name=model_name or derived_model,
        tool_definitions=_tool_definitions(tool_definitions) if tool_definitions else None,
    )
    return Trajectory(
        agent=agent,
        steps=steps,
        session_id=session_id,
        trajectory_id=trajectory_id,
        final_metrics=_final_metrics(messages, len(steps)),
    )
