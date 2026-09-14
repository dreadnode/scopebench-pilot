"""The in-container ScopeJudge agent application.

This is the explicit composition root for a sandboxed run. It owns model construction,
the Pydantic AI run/resume loop, optional ScopeJudge capability activation, trajectory
capture, and the stdin/result-file protocol. Docker remains responsible only for the
process and network isolation around this application.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import UsageLimits, capture_run_messages
from pydantic_ai.exceptions import UsageLimitExceeded

from agent_harness import (
    DeferredToolRequests,
    DeferredToolResults,
    HarnessDeps,
    HarnessSettings,
    agent,
    all_tool_definitions,
    trajectory_from_messages,
)
from scopejudge.capability import ScopeJudge
from scopejudge.protocol import AgentConfig, AgentResult, ToolCall

if TYPE_CHECKING:
    from pydantic_ai.capabilities import AgentCapability
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model

_WORK = Path("/work")
_FLAG_SRC = Path("/tmp/result.txt")  # noqa: S108 - the tasks' own flag contract
_FLAG_OUT = _WORK / "result.txt"
_RESULT_OUT = _WORK / "agent-result.json"
_TRAJECTORY_FILE = "trajectory.json"


class _RunOutcome(TypedDict):
    """The final text and tool-call trajectory from the agent loop."""

    output: str
    tool_calls: list[ToolCall]


_CONFIG_ADAPTER: TypeAdapter[AgentConfig] = TypeAdapter(AgentConfig)
_RESULT_ADAPTER: TypeAdapter[AgentResult] = TypeAdapter(AgentResult)


def _build_model(cfg: AgentConfig) -> Model:
    """Build the configured model while keeping its API key out of the environment."""
    from pydantic_ai.models import infer_model  # noqa: PLC0415
    from pydantic_ai.providers import infer_provider_class  # noqa: PLC0415

    raw = cfg["model"] or HarnessSettings().model
    return infer_model(
        raw,
        provider_factory=lambda provider: infer_provider_class(provider)(
            api_key=cfg["api_key"]  # pyright: ignore[reportCallIssue]
        ),
    )


def _tool_calls(messages: list[ModelMessage]) -> list[ToolCall]:
    """Extract complete tool-call arguments for host-side trajectory diagnostics."""
    return [
        ToolCall(name=part.tool_name, args=str(part.args))
        for message in messages
        for part in message.parts
        if part.part_kind == "tool-call"
    ]


def _agent_version() -> str:
    """Return the installed distribution version or ``unknown`` for source-only images."""
    with contextlib.suppress(PackageNotFoundError):
        return version("agent-harness")
    return "unknown"


def _write_trajectory(
    messages: list[ModelMessage],
    *,
    model: Model,
    cwd: Path,
    session_id: str | None,
    scopejudge_enabled: bool,
) -> None:
    """Write a best-effort ATIF trajectory without masking the run's own outcome."""
    if not messages:
        return
    with contextlib.suppress(Exception):
        trajectory = trajectory_from_messages(
            messages,
            agent_name="scopejudge" if scopejudge_enabled else "agent-harness",
            agent_version=_agent_version(),
            model_name=model.model_name,
            session_id=session_id,
            trajectory_id=str(uuid.uuid4()),
            tool_definitions=all_tool_definitions(),
        )
        _ = (cwd / _TRAJECTORY_FILE).write_text(
            trajectory.model_dump_json(exclude_none=True, indent=2),
            encoding="utf-8",
        )


def _capabilities(cfg: AgentConfig) -> tuple[AgentCapability[HarnessDeps], ...]:
    """Build the experimental treatment while leaving baseline runs unchanged."""
    if not cfg["scopejudge_enabled"]:
        return ()
    return (ScopeJudge(),)


async def run_agent(cfg: AgentConfig, *, model: Model, deps: HarnessDeps) -> _RunOutcome:
    """Drive the agent to completion and resolve unattended deferred requests inline."""
    usage_limits = UsageLimits(request_limit=cfg["request_limit"])
    reply = cfg["ask_user_reply"]
    capabilities = _capabilities(cfg)
    messages: list[ModelMessage] = []
    try:
        try:
            with capture_run_messages() as messages:
                run = await agent.run(
                    cfg["instruction"],
                    deps=deps,
                    usage_limits=usage_limits,
                    model=model,
                    capabilities=capabilities,
                )
        except UsageLimitExceeded:
            return _RunOutcome(
                output="Agent request budget exhausted before a final response.",
                tool_calls=_tool_calls(messages),
            )
        while isinstance(run.output, DeferredToolRequests):
            results = DeferredToolResults()
            for call in run.output.approvals:
                results.approvals[call.tool_call_id] = True
            for call in run.output.calls:
                results.calls[call.tool_call_id] = reply
            with capture_run_messages() as messages:
                run = await agent.run(
                    message_history=run.all_messages(),
                    deferred_tool_results=results,
                    deps=deps,
                    usage_limits=usage_limits,
                    model=model,
                    capabilities=capabilities,
                )
        return _RunOutcome(output=str(run.output), tool_calls=_tool_calls(run.all_messages()))
    finally:
        if cfg["atif"]:
            _write_trajectory(
                messages,
                model=model,
                cwd=deps.cwd,
                session_id=cfg["session_id"],
                scopejudge_enabled=cfg["scopejudge_enabled"],
            )


def _capture_flag(result: AgentResult) -> None:
    """Copy the task flag from the container namespace into the mounted workspace."""
    with contextlib.suppress(OSError):
        if _FLAG_SRC.is_file():
            text = _FLAG_SRC.read_text(encoding="utf-8", errors="replace")
            _ = _FLAG_OUT.write_text(text, encoding="utf-8")
            result.submitted = text.strip()
            return
    with contextlib.suppress(OSError):
        if _FLAG_OUT.is_file():
            result.submitted = _FLAG_OUT.read_text(encoding="utf-8", errors="replace").strip()


def _validation_summary(exc: ValidationError) -> str:
    """Summarize validation errors without echoing config values such as API keys."""
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or '<config>'}: {err['msg']}"
        for err in exc.errors(include_url=False)
    )


def _execute(cfg: AgentConfig, result: AgentResult) -> None:
    """Run ScopeJudge under its time budget and record the raw outcome."""
    deps = HarnessDeps(cwd=_WORK, reports_dir=_WORK / ".reports")
    try:
        model = _build_model(cfg)
        outcome = asyncio.run(
            asyncio.wait_for(run_agent(cfg, model=model, deps=deps), timeout=cfg["timeout"]),
        )
    except TimeoutError:
        result.error = f"agent exceeded {cfg['timeout']:.0f}s time budget"
    except Exception as exc:  # noqa: BLE001 - every failure must reach the host result
        result.error = f"{type(exc).__name__}: {exc}"
    else:
        result.output = outcome["output"]
        result.tool_calls = outcome["tool_calls"]


def main() -> int:
    """Read one config from stdin and always write one host-readable result file."""
    result = AgentResult(output="")
    try:
        try:
            cfg = _CONFIG_ADAPTER.validate_json(sys.stdin.read())
        except ValidationError as exc:
            result.error = f"invalid agent config: {_validation_summary(exc)}"
        except Exception as exc:  # noqa: BLE001 - broken stdin still produces a result
            result.error = f"invalid agent config: {type(exc).__name__}: {exc}"
        else:
            _execute(cfg, result)
    finally:
        _capture_flag(result)
        _ = _RESULT_OUT.write_bytes(_RESULT_ADAPTER.dump_json(result))
    return 0
