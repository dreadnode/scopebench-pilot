"""The ScopeJudge capability: the paper's post-tool judging extension point."""

from typing import override

from pydantic_ai import RunContext, ToolDefinition
from pydantic_ai.capabilities import AbstractCapability, ValidatedToolArgs
from pydantic_ai.messages import ToolCallPart

from agent_harness import HarnessDeps


class ScopeJudge(AbstractCapability[HarnessDeps]):
    """Judge completed function-tool calls before their results return to the agent.

    The pass-through implementation establishes the exact lifecycle boundary without
    changing baseline behavior. The ScopeJudge judging method belongs here: it receives
    the validated call and completed result once for every successfully executed
    function tool, and must return the result that should continue through the agent.
    """

    @override
    async def after_tool_execute(
        self,
        ctx: RunContext[HarnessDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        result: object,
    ) -> object:
        """Apply ScopeJudge after a tool completes; currently preserve its result unchanged."""
        del ctx, call, tool_def, args
        return result
