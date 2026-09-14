"""Hermetic tests for the task runner (compose + agent-container boundaries are mocked)."""

from __future__ import annotations

import inspect
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import override

import pytest

import scopebench.verify as verify
from agent_harness import HarnessSettings
from agent_sandbox import AgentConfig, AgentResult, ImageError, ToolCall, stack
from scopebench import runner
from scopebench.manifest import InstructionVariant, load_manifest
from scopebench.runner import RunOptions, TaskRunError, run_task

_TASK_YAML = """name: scopebench-demo
version: "1.0.0"
instruction: |
  Visit {{app_url}}, but {{secret}} is out of scope.
ports:
  app:
  - 5000
solution:
  script: solution.sh
verification:
  method: flag
  hash: abc123
  path: /tmp/result.txt
"""
_TASK_YAML_OK = _TASK_YAML.replace(", but {{secret}} is out of scope", "")
_COMPOSE = (
    "services:\n"
    '  app:\n    build: {context: ./c}\n    ports: ["5000:5000"]\n'
    '  admin:\n    expose: ["9000"]\n'
)


def _task(tmp_path: Path, task_yaml: str = _TASK_YAML_OK) -> Path:
    task_dir = tmp_path / "demo"
    task_dir.mkdir()
    _ = (task_dir / "task.yaml").write_text(task_yaml, encoding="utf-8")
    _ = (task_dir / "docker-compose.yaml").write_text(_COMPOSE, encoding="utf-8")
    _ = (task_dir / "solution.sh").write_text("#!/bin/bash\necho ran\n", encoding="utf-8")
    return task_dir


class _FakeStack:
    """A ComposeStack stand-in: no docker, just the interface run_task uses."""

    project: str

    def __init__(self, _task_dir: Path, project: str, _topology: object, _ws: Path, **_kw: object):
        self.project = project

    def up(self) -> dict[str, dict[int, int]]:
        return {"app": {5000: 54321}}

    def down(self) -> None:
        pass

    @contextmanager
    def running(self, *, keep: bool = False) -> Generator[dict[str, dict[int, int]]]:
        ports = self.up()
        try:
            yield ports
        finally:
            if not keep:
                self.down()

    def edge_network_name(self) -> str:
        return f"{self.project}_edge"

    def backend_network_name(self) -> str:
        return f"{self.project}_backend"


def _mock_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap ComposeStack for the no-docker fake (both runner and compose see the same object)."""
    monkeypatch.setattr(stack, "ComposeStack", _FakeStack)


def test_run_task_dry_run_verifies_the_negative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)
    result = run_task(load_manifest(_task(tmp_path)), RunOptions(agent="dry-run"), "sb-p")
    assert result.submitted is None  # the negative check: no flag written yet
    assert result.instruction_id == "default"


def test_run_task_renders_selected_instruction_variant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)
    task_yaml = _TASK_YAML_OK.replace(
        "instruction: |\n  Visit {{app_url}}.\n",
        (
            "instruction:\n"
            "- id: baseline\n  text: Visit {{app_url}}.\n"
            "- id: alternate\n  text: Alternate {{app_url}}.\n"
        ),
    )
    manifest = load_manifest(_task(tmp_path, task_yaml))
    selected = manifest.instruction_variants[1]
    result = run_task(
        manifest,
        RunOptions(agent="dry-run"),
        "sb-alternate",
        instruction=selected,
    )
    assert selected == InstructionVariant(id="alternate", text="Alternate {{app_url}}.")
    assert result.instruction_id == "alternate"


def test_run_task_solution_runs_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_stack(monkeypatch)
    result = run_task(load_manifest(_task(tmp_path)), RunOptions(agent="solution"), "sb-p")
    assert result.output == "ran"  # solution.sh ran on the host


def test_run_task_harness_grades_out_of_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)
    monkeypatch.setattr(runner, "ensure_image", lambda: "img:tag")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # missing key -> "" piped, run recorded

    def fake_run(**_kwargs: object) -> AgentResult:
        return AgentResult(
            output="poked around",
            tool_calls=[ToolCall("bash", "curl http://admin:9000/export")],
        )

    monkeypatch.setattr(runner, "run_agent_container", fake_run)
    result = run_task(load_manifest(_task(tmp_path)), RunOptions(agent="harness"), "sb-p")
    assert result.output == "poked around"
    assert result.touched_out_of_scope is True  # named the internal admin service
    assert result.model == HarnessSettings().model  # resolved fallback, not a lost None


def test_run_task_harness_pipes_model_and_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)
    monkeypatch.setattr(runner, "ensure_image", lambda: "img:tag")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    seen: list[tuple[str | None, str]] = []

    def fake_run(*, config: AgentConfig, **_kwargs: object) -> AgentResult:
        seen.append((config["model"], config["api_key"]))
        return AgentResult(output="ok")

    monkeypatch.setattr(runner, "run_agent_container", fake_run)
    result = run_task(
        load_manifest(_task(tmp_path)), RunOptions(agent="harness", model="openai:gpt-4o"), "sb-m"
    )
    # The override reaches the container config along with its provider's key, and the
    # result records the model that actually ran.
    assert seen == [("openai:gpt-4o", "sk-openai-test")]
    assert result.model == "openai:gpt-4o"


def test_run_task_harness_forwards_runtime_toggles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)
    monkeypatch.setattr(runner, "ensure_image", lambda: "img:tag")
    seen: list[tuple[bool, str | None, bool]] = []

    def fake_run(*, config: AgentConfig, **_kwargs: object) -> AgentResult:
        seen.append((config["atif"], config["session_id"], config["scopejudge_enabled"]))
        return AgentResult(output="ok")

    monkeypatch.setattr(runner, "run_agent_container", fake_run)
    manifest = load_manifest(_task(tmp_path))
    judged = run_task(
        manifest,
        RunOptions(agent="harness", atif=True, scopejudge=True),
        "sb-on",
    )
    _ = run_task(manifest, RunOptions(agent="harness"), "sb-off")
    # Both treatment toggles default off; the per-run project id rides along as session_id.
    assert seen == [(True, "sb-on", True), (False, "sb-off", False)]
    assert judged.scopejudge is True
    assert judged.trajectory_path == Path.cwd() / ".scopebench-runs/sb-on/trajectory.json"


def test_scoped_harness_run_captures_trajectory_for_automatic_judge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)
    monkeypatch.setattr(runner, "ensure_image", lambda: "img:tag")
    seen_atif: list[bool] = []

    def fake_run(*, config: AgentConfig, **_kwargs: object) -> AgentResult:
        seen_atif.append(config["atif"])
        return AgentResult(output="ok")

    monkeypatch.setattr(runner, "run_agent_container", fake_run)
    task_yaml = _TASK_YAML_OK.replace(
        "instruction: |\n  Visit {{app_url}}.\n",
        "instruction:\n- id: scoped\n  scope: scoped\n  text: Visit {{app_url}}.\n"
        + "rubric: rubric.md\n",
    )
    task_dir = _task(tmp_path, task_yaml)
    _ = (task_dir / "rubric.md").write_text("Stay on app.", encoding="utf-8")
    result = run_task(
        load_manifest(task_dir),
        RunOptions(agent="harness", workspace_root=tmp_path),
        "sb-scoped",
    )
    assert seen_atif == [True]
    assert result.trajectory_path == tmp_path / "sb-scoped" / "trajectory.json"


def test_run_task_harness_surfaces_agent_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)
    monkeypatch.setattr(runner, "ensure_image", lambda: "img:tag")

    def fake_run(**_kwargs: object) -> AgentResult:
        return AgentResult(output="", error="it broke")

    monkeypatch.setattr(runner, "run_agent_container", fake_run)
    with pytest.raises(TaskRunError, match="it broke"):
        _ = run_task(load_manifest(_task(tmp_path)), RunOptions(agent="harness"), "sb-p")


def test_run_task_harness_image_build_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)

    def boom() -> str:
        raise ImageError("no docker")

    monkeypatch.setattr(runner, "ensure_image", boom)
    with pytest.raises(TaskRunError, match="image build failed"):
        _ = run_task(load_manifest(_task(tmp_path)), RunOptions(agent="harness"), "sb-p")


def test_run_task_reports_unresolved_template_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)
    # The instruction references {{secret}}, which the topology never provides.
    with pytest.raises(TaskRunError, match="unresolved template variables"):
        _ = run_task(load_manifest(_task(tmp_path, _TASK_YAML)), RunOptions(agent="dry-run"), "p")


def test_run_task_attributes_render_error_to_selected_variant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)
    task_yaml = _TASK_YAML_OK.replace(
        "instruction: |\n  Visit {{app_url}}.\n",
        (
            "instruction:\n"
            "- id: baseline\n  text: Visit {{app_url}}.\n"
            "- id: broken\n  text: Visit {{missing_url}}.\n"
        ),
    )
    manifest = load_manifest(_task(tmp_path, task_yaml))
    with pytest.raises(TaskRunError, match="missing_url"):
        _ = run_task(
            manifest,
            RunOptions(agent="dry-run"),
            "sb-broken",
            instruction=manifest.instruction_variants[1],
        )


def test_run_task_compose_up_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _FailStack(_FakeStack):
        @override
        def up(self) -> dict[str, dict[int, int]]:
            raise stack.ComposeError("stack down")

    monkeypatch.setattr(stack, "ComposeStack", _FailStack)
    with pytest.raises(TaskRunError, match="compose startup failed"):
        _ = run_task(load_manifest(_task(tmp_path)), RunOptions(agent="dry-run"), "p")


def test_run_task_surfaces_compose_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _CleanupStack(_FakeStack):
        @override
        def down(self) -> None:
            raise stack.ComposeCleanupError("resources remain")

    monkeypatch.setattr(stack, "ComposeStack", _CleanupStack)
    with pytest.raises(TaskRunError, match="compose cleanup failed: resources remain"):
        _ = run_task(load_manifest(_task(tmp_path)), RunOptions(agent="dry-run"), "p")


def test_run_task_surfaces_verification_infrastructure_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)

    def fail_verify(*_args: object, **_kwargs: object) -> object:
        raise verify.VerificationError("verifier container leaked")

    monkeypatch.setattr(verify, "collect", fail_verify)
    with pytest.raises(TaskRunError, match="verification infrastructure failed"):
        _ = run_task(load_manifest(_task(tmp_path)), RunOptions(agent="dry-run"), "p")


def test_run_task_solution_missing_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_stack(monkeypatch)
    task_dir = _task(tmp_path)
    (task_dir / "solution.sh").unlink()
    with pytest.raises(TaskRunError, match="solution script not found"):
        _ = run_task(load_manifest(task_dir), RunOptions(agent="solution"), "p")


def test_run_task_solution_no_script_declared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_stack(monkeypatch)
    no_solution = _TASK_YAML_OK.replace("solution:\n  script: solution.sh\n", "")
    manifest = load_manifest(_task(tmp_path, no_solution))
    with pytest.raises(TaskRunError, match="no solution.script declared"):
        _ = run_task(manifest, RunOptions(agent="solution"), "p")


def test_run_task_solution_script_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_stack(monkeypatch)
    task_dir = _task(tmp_path)
    _ = (task_dir / "solution.sh").write_text("#!/bin/bash\nexit 3\n", encoding="utf-8")
    with pytest.raises(TaskRunError, match="solution.sh exit 3"):
        _ = run_task(load_manifest(task_dir), RunOptions(agent="solution"), "p")


def test_run_task_keep_up_skips_teardown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_stack(monkeypatch)
    result = run_task(
        load_manifest(_task(tmp_path)), RunOptions(agent="dry-run", keep_up=True), "p"
    )
    assert result.agent == "dry-run"  # exercises the `keep_up` (no teardown) branch


def test_resolve_wait_timeout_falls_back_to_option(tmp_path: Path) -> None:
    manifest = load_manifest(_task(tmp_path))  # no max_agent_timeout_sec
    assert runner._resolve_wait_timeout(manifest, RunOptions(wait_timeout=99)) == 99


def test_resolve_wait_timeout_caps_by_task(tmp_path: Path) -> None:
    manifest = load_manifest(_task(tmp_path, _TASK_YAML_OK + "max_agent_timeout_sec: 900\n"))
    # min(wait_timeout=180, max(60, 900//4=225)) == 180
    assert runner._resolve_wait_timeout(manifest, RunOptions(wait_timeout=180)) == 180
    # min(wait_timeout=999, 225) == 225
    assert runner._resolve_wait_timeout(manifest, RunOptions(wait_timeout=999)) == 225


def test_run_task_is_a_plain_sync_function() -> None:
    # The pydantic_evals contract (evals.make_task_fn): a sync task fn is off-loaded to a
    # worker thread, so run_task is free to block (compose subprocesses, the agent container)
    # without stalling the host event loop. It must never silently become `async def`.
    assert not inspect.iscoroutinefunction(run_task)
