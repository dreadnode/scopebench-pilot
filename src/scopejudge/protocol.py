"""The host/container wire contract for a ScopeJudge run.

These types are deliberately dependency-light. The host-side :mod:`agent_sandbox`
runner and the in-container ScopeJudge application import the same definitions, so
the JSON contract cannot drift between duplicated models.
"""

from dataclasses import dataclass, field
from typing import TypedDict


class AgentConfig(TypedDict):
    """Configuration piped to the ScopeJudge container on standard input."""

    instruction: str
    model: str | None
    api_key: str
    request_limit: int
    timeout: float
    ask_user_reply: str
    atif: bool
    session_id: str | None
    scopejudge_enabled: bool


@dataclass
class ToolCall:
    """One tool invocation captured from the agent's message history."""

    name: str
    args: str


@dataclass
class AgentResult:
    """The raw result written by ScopeJudge and read by the sandbox host."""

    output: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None
    submitted: str | None = None
