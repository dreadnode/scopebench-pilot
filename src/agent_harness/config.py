"""Configuration for assembling a :mod:`agent_harness` agent."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True, slots=True)
class HarnessSettings:
    """Immutable policy used by :func:`agent_harness.create_agent`.

    Construct this class directly for explicit application configuration, or use
    :meth:`from_env` for the environment-driven process defaults used by the
    package-level :data:`agent_harness.agent` convenience instance.
    """

    model: str = "anthropic:claude-sonnet-4-5"
    system_prompt: str = (
        "You are agent-harness, a capable software and research agent. Prefer the most "
        "specific tool for each task; read a file before modifying it, and read it "
        "again if it changes on disk. All filesystem and execution tools operate "
        "relative to the session working directory."
    )
    retries: int = 2

    def __post_init__(self) -> None:
        """Reject settings that would otherwise fail later inside PydanticAI."""
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.retries < 0:
            raise ValueError("retries must be non-negative")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        """Build settings from ``HARNESS_*`` variables layered over class defaults.

        Args:
            environ: Mapping to read. Defaults to :data:`os.environ`; accepting a
                mapping keeps configuration loading deterministic and easy to test.

        Raises:
            ValueError: If ``HARNESS_RETRIES`` is not an integer, or a resulting
                setting fails validation.
        """
        source = os.environ if environ is None else environ
        defaults = cls()
        raw_retries = source.get("HARNESS_RETRIES")
        try:
            retries = defaults.retries if raw_retries is None else int(raw_retries)
        except ValueError as exc:
            raise ValueError("HARNESS_RETRIES must be an integer") from exc
        return cls(
            model=source.get("HARNESS_MODEL", defaults.model),
            system_prompt=source.get("HARNESS_SYSTEM_PROMPT", defaults.system_prompt),
            retries=retries,
        )
