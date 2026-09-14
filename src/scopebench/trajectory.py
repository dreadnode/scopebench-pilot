"""Read-only inspection of a complete ATIF trajectory for scope judging."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField

from agent_harness.atif import Trajectory

_MAX_PAGE_SIZE = 100
_MIN_ARGUMENT_CHARS = 500
_MAX_ARGUMENT_CHARS = 50_000
_MIN_CONTENT_CHARS = 1_000
_MAX_CONTENT_CHARS = 100_000


class _InspectionModel(BaseModel):
    """Immutable base for structured values returned to the judge tools."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CallSummary(_InspectionModel):
    """Compact tool-call representation returned to the judge."""

    call_index: int
    call_id: str
    function_name: str
    arguments: str
    arguments_truncated: bool
    assistant_message: str
    has_output: bool


class CallPage(_InspectionModel):
    """One bounded page of trajectory calls."""

    total_calls: int
    offset: int
    calls: list[CallSummary]
    next_offset: int | None


class SearchMatch(_InspectionModel):
    """One whole-trajectory search match."""

    call_index: int
    call_id: str
    function_name: str
    match: str


type SearchField = Literal["arguments", "outputs"]


class SearchResults(_InspectionModel):
    """Whole-trajectory search response."""

    status: Literal["ok"] = "ok"
    query: str
    field: SearchField
    matches: list[SearchMatch]
    limit: int


class NearbyCall(_InspectionModel):
    """Identity of a call adjacent to retrieved evidence."""

    call_index: int
    call_id: str
    function_name: str


class CallContext(_InspectionModel):
    """Full bounded evidence for one trajectory call."""

    status: Literal["ok"] = "ok"
    call_index: int
    step_id: int
    call_id: str
    function_name: str
    arguments: str
    arguments_truncated: bool
    output: str | None
    output_truncated: bool
    assistant_message: str
    nearby_calls: list[NearbyCall]


class InspectionError(_InspectionModel):
    """Structured failure returned when a judge requests an invalid inspection."""

    status: Literal["error"] = "error"
    error: str
    query: str | None = None
    call_index: int | None = None


type SearchOutcome = Annotated[
    SearchResults | InspectionError, PydanticField(discriminator="status")
]
type CallContextOutcome = Annotated[
    CallContext | InspectionError, PydanticField(discriminator="status")
]


class BatchSearchResults(_InspectionModel):
    """Results of searching all call arguments for several indicators."""

    searched_all_call_arguments: Literal[True] = True
    results: list[SearchOutcome]


@dataclass(frozen=True)
class IndexedToolCall:
    """A tool call and the context needed to assess its scope."""

    index: int
    step_id: int
    call_id: str
    function_name: str
    arguments: dict[str, object]
    output: str | None
    assistant_message: str

    @property
    def arguments_text(self) -> str:
        """Serialize arguments deterministically for search and display."""
        return json.dumps(self.arguments, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class TrajectoryView:
    """Provider-neutral, indexed view over one ATIF trajectory."""

    source_path: Path
    trajectory_id: str
    user_intent: str
    calls: tuple[IndexedToolCall, ...]

    @classmethod
    def from_path(cls, path: Path) -> TrajectoryView:
        """Load the single ATIF document emitted by the local harness."""
        trajectory = Trajectory.model_validate_json(path.read_bytes())
        outputs: dict[str, deque[str]] = defaultdict(deque)
        for step in trajectory.steps:
            if step.observation is None:
                continue
            for result in step.observation.results:
                if result.source_call_id is not None:
                    outputs[result.source_call_id].append(result.content or "")

        calls: list[IndexedToolCall] = []
        for step in trajectory.steps:
            if step.source != "agent" or step.tool_calls is None:
                continue
            for call in step.tool_calls:
                available_outputs = outputs[call.tool_call_id]
                calls.append(
                    IndexedToolCall(
                        index=len(calls) + 1,
                        step_id=step.step_id,
                        call_id=call.tool_call_id,
                        function_name=call.function_name,
                        arguments=call.arguments,
                        output=available_outputs.popleft() if available_outputs else None,
                        assistant_message=step.message,
                    )
                )

        user_intent = next(
            (step.message for step in trajectory.steps if step.source == "user"),
            "",
        )
        return cls(
            source_path=path,
            trajectory_id=trajectory.session_id or trajectory.trajectory_id or path.stem,
            user_intent=user_intent,
            calls=tuple(calls),
        )

    def call(self, index: int) -> IndexedToolCall:
        """Return a call by its one-based judge-visible index."""
        if index < 1 or index > len(self.calls):
            raise ValueError(f"call_index must be between 1 and {len(self.calls)}")
        return self.calls[index - 1]

    def list_calls(
        self,
        *,
        offset: int = 0,
        limit: int = 25,
        max_argument_chars: int = 4_000,
    ) -> CallPage:
        """List a bounded page of calls."""
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= _MAX_PAGE_SIZE:
            raise ValueError("limit must be between 1 and 100")
        if not _MIN_ARGUMENT_CHARS <= max_argument_chars <= _MAX_ARGUMENT_CHARS:
            raise ValueError("max_argument_chars must be between 500 and 50000")

        summaries: list[CallSummary] = []
        selected = self.calls[offset : offset + limit]
        for call in selected:
            arguments, truncated = _clip(call.arguments_text, max_argument_chars)
            message, _ = _clip(call.assistant_message, 2_000)
            summaries.append(
                CallSummary(
                    call_index=call.index,
                    call_id=call.call_id,
                    function_name=call.function_name,
                    arguments=arguments,
                    arguments_truncated=truncated,
                    assistant_message=message,
                    has_output=call.output is not None,
                )
            )
        next_offset = offset + len(selected)
        return CallPage(
            total_calls=len(self.calls),
            offset=offset,
            calls=summaries,
            next_offset=next_offset if next_offset < len(self.calls) else None,
        )

    def search(
        self,
        query: str,
        *,
        field: str,
        regex: bool = False,
        limit: int = 20,
    ) -> SearchResults:
        """Search every call argument or output, returning navigational snippets."""
        if not query:
            raise ValueError("query must not be empty")
        if field == "arguments":
            search_field: SearchField = "arguments"
        elif field == "outputs":
            search_field = "outputs"
        else:
            raise ValueError("field must be arguments or outputs")
        if not 1 <= limit <= _MAX_PAGE_SIZE:
            raise ValueError("limit must be between 1 and 100")
        pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
        matches: list[SearchMatch] = []
        for call in self.calls:
            haystack = call.arguments_text if search_field == "arguments" else (call.output or "")
            match = pattern.search(haystack)
            if match is None:
                continue
            start = max(0, match.start() - 180)
            end = min(len(haystack), match.end() + 420)
            matches.append(
                SearchMatch(
                    call_index=call.index,
                    call_id=call.call_id,
                    function_name=call.function_name,
                    match=haystack[start:end],
                )
            )
            if len(matches) == limit:
                break
        return SearchResults(query=query, field=search_field, matches=matches, limit=limit)

    def call_context(
        self,
        index: int,
        *,
        max_content_chars: int = 20_000,
    ) -> CallContext:
        """Retrieve one call's full arguments, output, and adjacent call identities."""
        if not _MIN_CONTENT_CHARS <= max_content_chars <= _MAX_CONTENT_CHARS:
            raise ValueError("max_content_chars must be between 1000 and 100000")
        target = self.call(index)
        arguments, arguments_truncated = _clip(target.arguments_text, max_content_chars)
        output, output_truncated = _clip(target.output or "", max_content_chars)
        start = max(0, target.index - 2)
        end = min(len(self.calls), target.index + 1)
        nearby = [
            NearbyCall(
                call_index=call.index,
                call_id=call.call_id,
                function_name=call.function_name,
            )
            for call in self.calls[start:end]
            if call.index != target.index
        ]
        return CallContext(
            call_index=target.index,
            step_id=target.step_id,
            call_id=target.call_id,
            function_name=target.function_name,
            arguments=arguments,
            arguments_truncated=arguments_truncated,
            output=output if target.output is not None else None,
            output_truncated=output_truncated,
            assistant_message=target.assistant_message,
            nearby_calls=nearby,
        )


@dataclass
class TrajectoryInspector:
    """Per-run review state layered over an immutable :class:`TrajectoryView`."""

    trajectory: TrajectoryView
    reviewed_call_indices: set[int] = field(default_factory=set)
    inspection_count: int = 0

    def list_calls(
        self,
        *,
        offset: int = 0,
        limit: int = 25,
        max_argument_chars: int = 4_000,
    ) -> CallPage:
        """Return a call page and record every fully visible argument as reviewed."""
        page = self.trajectory.list_calls(
            offset=offset,
            limit=limit,
            max_argument_chars=max_argument_chars,
        )
        self.reviewed_call_indices.update(
            call.call_index for call in page.calls if not call.arguments_truncated
        )
        self.inspection_count += 1
        return page

    def search(
        self,
        query: str,
        *,
        field: str,
        regex: bool = False,
        limit: int = 20,
    ) -> SearchResults:
        """Search the complete trajectory and record the inspection attempt."""
        self.inspection_count += 1
        return self.trajectory.search(query, field=field, regex=regex, limit=limit)

    def call_context(self, index: int, *, max_content_chars: int = 20_000) -> CallContext:
        """Return one call's evidence and mark complete arguments as reviewed."""
        context = self.trajectory.call_context(index, max_content_chars=max_content_chars)
        if not context.arguments_truncated:
            self.reviewed_call_indices.add(index)
        self.inspection_count += 1
        return context


def _clip(value: str, limit: int) -> tuple[str, bool]:
    """Clip large evidence with an explicit omission marker."""
    if len(value) <= limit:
        return value, False
    return value[:limit] + f"\n... [{len(value) - limit} characters omitted]", True
