"""Hermetic tests for the `scopebench` CLI; run_evals and image build are mocked."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_sandbox import ImageError
from scopebench import __main__ as cli
from scopebench.__main__ import main

_TASK = 'name: x\nversion: "1"\ninstruction: hi\nverification:\n  method: flag\n  hash: v\n'


class _FakeReport:
    failures: list[object]

    def __init__(self, failures: tuple[object, ...] = ()) -> None:
        self.failures = list(failures)

    def print(self, **_kwargs: object) -> None:
        pass

    def averages(self) -> None:
        return None


def _tasks_dir(tmp_path: Path) -> Path:
    root = tmp_path / "tasks"
    (root / "demo").mkdir(parents=True)
    _ = (root / "demo" / "task.yaml").write_text(_TASK, encoding="utf-8")
    return root


def _mock_eval(monkeypatch: pytest.MonkeyPatch, report: _FakeReport | None = None) -> None:
    result = report or _FakeReport()

    async def fake_run_evals(*_args: object, **_kwargs: object) -> _FakeReport:
        return result

    def no_logfire(*_args: object) -> None:
        pass

    monkeypatch.setattr(cli, "run_evals", fake_run_evals)
    monkeypatch.setattr(cli, "configure_logfire", no_logfire)


def test_main_bad_tasks_dir(tmp_path: Path) -> None:
    assert main(["--all", "--tasks-dir", str(tmp_path / "nope")]) == 2


def test_main_no_tasks_dir(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_load_dotenv", _noop_dotenv)
    monkeypatch.delenv("SCOPEBENCH_TASKS_DIR", raising=False)
    assert main(["--all"]) == 2
    assert "SCOPEBENCH_TASKS_DIR" in capsys.readouterr().err


def test_main_no_selector(tmp_path: Path) -> None:
    assert main(["--tasks-dir", str(_tasks_dir(tmp_path))]) == 2


def test_main_list(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["--list", "--tasks-dir", str(_tasks_dir(tmp_path))]) == 0
    output = capsys.readouterr().out
    assert "demo" in output
    assert "1 prompt" in output


def test_main_list_shows_multiple_prompts(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    tasks_dir = _tasks_dir(tmp_path)
    manifest = tasks_dir / "demo" / "task.yaml"
    _ = manifest.write_text(
        _TASK.replace(
            "instruction: hi",
            "instruction:\n- {id: first, text: one}\n- {id: second, text: two}",
        ),
        encoding="utf-8",
    )
    assert main(["--list", "--tasks-dir", str(tasks_dir)]) == 0
    assert "2 prompts" in capsys.readouterr().out


def test_main_solution_returns_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_eval(monkeypatch)
    assert main(["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--agent", "solution"]) == 0


def test_main_returns_one_on_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_eval(monkeypatch, _FakeReport(failures=("boom",)))
    assert main(["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--agent", "solution"]) == 1


def test_main_task_selector(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_eval(monkeypatch)
    argv = ["--task", "demo", "--tasks-dir", str(_tasks_dir(tmp_path)), "--agent", "solution"]
    assert main(argv) == 0


def test_main_unknown_task_name(tmp_path: Path) -> None:
    argv = ["--task", "nope", "--tasks-dir", str(_tasks_dir(tmp_path)), "--agent", "solution"]
    with pytest.raises(SystemExit, match="no such task"):
        _ = main(argv)


def test_main_json_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_eval(monkeypatch)

    def to_json(_report: object) -> dict[str, object]:
        return {"ok": True}

    monkeypatch.setattr(cli, "report_to_json", to_json)
    out = tmp_path / "report.json"
    argv = ["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--agent", "solution"]
    assert main([*argv, "--json", str(out)]) == 0
    assert out.is_file()


def test_main_harness_builds_image(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_eval(monkeypatch)
    built: list[int] = []

    def fake_image() -> str:
        built.append(1)
        return "img"

    monkeypatch.setattr(cli, "ensure_image", fake_image)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert main(["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--agent", "harness"]) == 0
    assert built


def test_main_defaults_to_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without --agent the harness strategy runs (proved by the image build)."""
    _mock_eval(monkeypatch)
    built: list[int] = []

    def fake_image() -> str:
        built.append(1)
        return "img"

    monkeypatch.setattr(cli, "ensure_image", fake_image)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert main(["--all", "--tasks-dir", str(_tasks_dir(tmp_path))]) == 0
    assert built


def test_main_harness_errors_without_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _mock_eval(monkeypatch)
    monkeypatch.setattr(cli, "ensure_image", lambda: "img")
    monkeypatch.setattr(cli, "_load_dotenv", _noop_dotenv)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--agent", "harness"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_main_harness_errors_without_openai_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_load_dotenv", _noop_dotenv)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    argv = ["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--model", "openai:gpt-4o"]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "openai:gpt-4o" in err
    assert "OPENAI_API_KEY" in err


def test_main_harness_runs_openai_model_with_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_eval(monkeypatch)
    monkeypatch.setattr(cli, "ensure_image", lambda: "img")
    monkeypatch.setattr(cli, "_load_dotenv", _noop_dotenv)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "judge-k")
    argv = ["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--model", "openai:gpt-4o"]
    assert main(argv) == 0


def test_main_harness_errors_without_trajectory_judge_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_load_dotenv", _noop_dotenv)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tasks_dir = _tasks_dir(tmp_path)
    _ = (tasks_dir / "demo/rubric.md").write_text("Stay scoped.", encoding="utf-8")
    _ = (tasks_dir / "demo/task.yaml").write_text(
        _TASK.replace(
            "instruction: hi",
            "instruction:\n- id: scoped\n  scope: scoped\n  text: hi\nrubric: rubric.md",
        ),
        encoding="utf-8",
    )
    argv = ["--all", "--tasks-dir", str(tasks_dir), "--model", "openai:gpt-4o"]
    assert main(argv) == 2
    assert "automatic scoped trajectory judging" in capsys.readouterr().err


def test_main_harness_rejects_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_load_dotenv", _noop_dotenv)
    argv = ["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--model", "groq:llama-3.3-70b"]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "'groq'" in err
    assert "Supported providers: anthropic, openai, google" in err


def test_main_harness_rejects_bare_model_string(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_load_dotenv", _noop_dotenv)
    argv = ["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--model", "claude-sonnet-4-5"]
    assert main(argv) == 2
    assert "provider:model" in capsys.readouterr().err


def test_main_harness_steers_openai_chat_to_responses_prefix(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_load_dotenv", _noop_dotenv)
    argv = ["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--model", "openai-chat:gpt-4o"]
    assert main(argv) == 2
    assert "use the 'openai:' prefix" in capsys.readouterr().err


def test_main_list_models(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_load_dotenv", _noop_dotenv)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["--list-models"]) == 0  # exits before any tasks-dir or key checks
    out = capsys.readouterr().out
    assert "anthropic:claude-sonnet-4-5" in out
    assert "openai:gpt-4o" in out
    assert "[ANTHROPIC_API_KEY: configured]" in out
    assert "[OPENAI_API_KEY: missing]" in out


def test_main_atif_warns_for_non_harness_agent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _mock_eval(monkeypatch)
    argv = ["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--agent", "solution", "--atif"]
    assert main(argv) == 0
    assert "--atif only produces trajectories with --agent harness" in capsys.readouterr().err


def test_main_scopejudge_warns_for_non_harness_agent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _mock_eval(monkeypatch)
    argv = [
        "--all",
        "--tasks-dir",
        str(_tasks_dir(tmp_path)),
        "--agent",
        "solution",
        "--scopejudge",
    ]
    assert main(argv) == 0
    assert "--scopejudge only applies to --agent harness" in capsys.readouterr().err


def test_main_concurrency_warns_for_non_harness_agent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _mock_eval(monkeypatch)
    argv = ["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--agent", "solution"]
    assert main([*argv, "--concurrency", "4"]) == 0
    assert "--concurrency 4 is ignored for --agent solution" in capsys.readouterr().err


def test_main_harness_image_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom() -> str:
        raise ImageError("no docker")

    monkeypatch.setattr(cli, "ensure_image", boom)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert main(["--all", "--tasks-dir", str(_tasks_dir(tmp_path)), "--agent", "harness"]) == 2


def test_default_tasks_dir_none_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOPEBENCH_TASKS_DIR", raising=False)
    assert cli._default_tasks_dir() is None


def test_default_tasks_dir_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCOPEBENCH_TASKS_DIR", str(tmp_path))
    assert cli._default_tasks_dir() == tmp_path


def test_load_dotenv_sets_missing_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _ = (tmp_path / ".env").write_text(
        (
            '# comment\n\nFOO=bar # inline comment\nNOEQUALS\nQUOTED="q"\n'
            "export EXPORTED='from dotenv'\nBASE=/tasks\nEXPANDED=${BASE}/demo\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("EXPORTED", raising=False)
    monkeypatch.delenv("BASE", raising=False)
    monkeypatch.delenv("EXPANDED", raising=False)
    monkeypatch.setenv("QUOTED", "already-set")
    cli._load_dotenv(tmp_path)
    assert os.environ["FOO"] == "bar"
    assert os.environ["EXPORTED"] == "from dotenv"
    assert os.environ["EXPANDED"] == "/tasks/demo"
    assert os.environ["QUOTED"] == "already-set"  # real env wins (override=False)


def test_load_dotenv_absent_is_noop(tmp_path: Path) -> None:
    cli._load_dotenv(tmp_path)  # no .env -> silently does nothing


def test_main_loads_dotenv_from_current_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded: list[Path] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_load_dotenv", loaded.append)
    monkeypatch.setattr(cli, "format_model_listing", lambda: "models")
    assert main(["--list-models"]) == 0
    assert loaded == [tmp_path]


def _noop_dotenv(_root: Path) -> None:
    pass
