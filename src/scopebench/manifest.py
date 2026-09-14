"""Parse a ScopeBench ``task.yaml`` into a typed manifest.

Only the fields the platform reads are modelled; unknown keys are ignored so an external task
schema can evolve independently of this repository.
The exception is the ``network:`` block (:class:`agent_sandbox.NetworkSpec`), which rejects
unknown keys so a typo fails loudly at load time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, ClassVar, Literal

import yaml
from pydantic import (
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from agent_sandbox import NetworkSpec

type ScopeKind = Literal["raw", "scoped"]


class InstructionVariant(BaseModel):
    """One named agent-facing prompt from a task's ``instruction`` field."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
        description="Stable kebab-case identifier used in reports and run artifacts.",
    )
    scope: ScopeKind | None = Field(
        default=None,
        description=(
            "Whether this prompt is the unrestricted capability baseline or a scope-bearing "
            "treatment. Legacy variants may omit it, but are not trajectory-judged."
        ),
    )
    text: str = Field(min_length=1, description="Agent-facing prompt with optional templates.")

    @field_validator("text")
    @classmethod
    def _text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instruction text must not be blank")
        return value


class _Verification(BaseModel):
    """Shared immutable base for the two supported verification contracts."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class FlagVerification(_Verification):
    """A forbidden flag identified by its SHA-256 digest."""

    method: Literal["flag"]
    path: str | None = Field(
        default=None, description="Optional host-side result path declared by the task."
    )
    hash: str = Field(
        min_length=1,
        description="Expected SHA-256 digest, written as ``sha256:<hex>`` or bare hex.",
    )

    @field_validator("hash")
    @classmethod
    def _normalize_sha256(cls, value: str) -> str:
        algorithm, separator, digest = value.strip().partition(":")
        if separator and algorithm.lower() != "sha256":
            raise ValueError(f"unsupported hash algorithm: {algorithm!r}; expected sha256")
        normalized = digest if separator else algorithm
        if not normalized:
            raise ValueError("sha256 digest must not be blank")
        return normalized.lower()


class ScriptVerification(_Verification):
    """A verifier script whose zero exit status means the forbidden objective was reached."""

    method: Literal["script"]
    script: str = Field(min_length=1, description="Verifier path relative to the task directory.")
    where: Literal["host", "agent"] = Field(
        default="host",
        description="Run on the host or in a throwaway container on the backend network.",
    )
    timeout: int = Field(default=30, gt=0, description="Verifier timeout in seconds.")

    @field_validator("where", mode="before")
    @classmethod
    def _null_where_means_host(cls, value: object) -> object:
        """Preserve the historical behavior of an explicit ``where: null``."""
        return "host" if value is None else value


type Verification = FlagVerification | ScriptVerification


class TaskManifest(BaseModel):
    """A parsed ScopeBench task: the task.yaml fields the platform reads, plus its directory."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, validate_by_name=True)

    name: str = Field(min_length=1, description="The manifest ``name``.")
    dir: Path = Field(
        description="Absolute path to the task directory (injected by the loader, not a "
        + "task.yaml field)."
    )
    instruction: str | tuple[InstructionVariant, ...] = Field(
        description="One agent-facing prompt or a non-empty list of named prompt variants, "
        + "still containing ``{{var}}`` slots."
    )
    rubric: str | None = Field(
        default=None,
        description=(
            "Evaluator-only scope rubric path relative to the task directory. It is never "
            "rendered into the acting agent's prompt."
        ),
    )
    verification: Annotated[
        FlagVerification | ScriptVerification,
        Field(discriminator="method", description="How the task is graded."),
    ]
    solution_script: str | None = Field(
        default=None,
        validation_alias=AliasPath("solution", "script"),
        description="Reference solution path relative to ``dir`` (or None).",
    )
    max_agent_timeout_sec: int | None = Field(
        default=None, description="Advisory evaluation timeout, if declared."
    )
    network: NetworkSpec | None = Field(
        default=None,
        description="Parsed ``network:`` block (the task's realistic-DNS story), or None.",
    )
    difficulty: str | None = Field(
        default=None, description="Author-declared difficulty label, if any."
    )

    @property
    def slug(self) -> str:
        """The task's directory name (its stable short id)."""
        return self.dir.name

    @property
    def instruction_variants(self) -> tuple[InstructionVariant, ...]:
        """Return a uniform tuple of prompts, synthesizing ``default`` for scalar tasks."""
        if isinstance(self.instruction, str):
            return (InstructionVariant(id="default", text=self.instruction),)
        return self.instruction

    @property
    def canonical_instruction(self) -> InstructionVariant:
        """The prompt used by consumers that execute only one instruction."""
        return self.instruction_variants[0]

    @property
    def rubric_path(self) -> Path | None:
        """Return the resolved evaluator-only rubric path, when one is declared."""
        if self.rubric is None:
            return None
        return (self.dir / self.rubric).resolve()

    def read_rubric(self) -> str | None:
        """Read the evaluator-only scope rubric without exposing it to the acting agent."""
        path = self.rubric_path
        return path.read_text(encoding="utf-8") if path is not None else None

    @field_validator("instruction")
    @classmethod
    def _validate_instruction(
        cls, value: str | tuple[InstructionVariant, ...]
    ) -> str | tuple[InstructionVariant, ...]:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("instruction must not be blank")
            return value
        if not value:
            raise ValueError("instruction list must contain at least one variant")
        ids = [variant.id for variant in value]
        duplicates = sorted({variant_id for variant_id in ids if ids.count(variant_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate instruction ids: {', '.join(duplicates)}")
        return value


# Shallow str-keyed view of the parsed YAML document, so the ``dir`` injection below stays typed
# without a cast.
_MAPPING_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


def load_manifest(task_dir: Path) -> TaskManifest:
    """Load and validate the ``task.yaml`` under ``task_dir``.

    Args:
        task_dir: Directory containing a ``task.yaml``.

    Returns:
        The parsed :class:`TaskManifest`.

    Raises:
        FileNotFoundError: If no ``task.yaml`` exists in ``task_dir``.
        ValueError: If required fields are missing or malformed (the error names the file).
    """
    task_dir = task_dir.resolve()
    manifest_path = task_dir / "task.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no task.yaml in {task_dir}")

    loaded: object = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    try:
        payload = _MAPPING_ADAPTER.validate_python(loaded)
        manifest = TaskManifest.model_validate({**payload, "dir": task_dir})
    except ValidationError as exc:
        raise ValueError(f"{manifest_path}: {exc}") from exc
    rubric_path = manifest.rubric_path
    if rubric_path is not None:
        if not rubric_path.is_relative_to(task_dir):
            raise ValueError(f"{manifest_path}: rubric must stay within the task directory")
        if not rubric_path.is_file():
            raise ValueError(f"{manifest_path}: rubric file not found: {rubric_path}")
    if (
        any(variant.scope == "scoped" for variant in manifest.instruction_variants)
        and rubric_path is None
    ):
        raise ValueError(f"{manifest_path}: scoped instructions require an evaluator rubric")
    return manifest


def discover_tasks(tasks_dir: Path) -> list[TaskManifest]:
    """Load every task directly under ``tasks_dir`` that has a ``task.yaml``.

    Args:
        tasks_dir: Any directory containing task directories.

    Returns:
        Manifests sorted by directory name. Directories without a ``task.yaml``
        (e.g. the ``CANARY`` anchor) are skipped.
    """
    return [
        load_manifest(child)
        for child in sorted(tasks_dir.iterdir())
        if child.is_dir() and (child / "task.yaml").is_file()
    ]
