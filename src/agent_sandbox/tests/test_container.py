"""Hermetic tests for the host-side container runner (the docker CLI is mocked)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import cast

import pytest

from agent_sandbox import AgentConfig, agent, run_agent_container
from agent_sandbox import _docker as docker


def _cfg(timeout: float = 60.0) -> AgentConfig:
    return AgentConfig(
        instruction="i",
        model=None,
        api_key="secret-key",
        request_limit=5,
        timeout=timeout,
        ask_user_reply="r",
        atif=False,
        session_id="sess-1",
        scopejudge_enabled=False,
    )


def test_run_cmd_is_hardened() -> None:
    cmd = agent._run_cmd("img", "c1", "netname", Path("/tmp/ws"))  # noqa: S108
    assert cmd[:2] == ["docker", "run"]
    for flag in ("--rm", "--cap-drop", "--security-opt", "--user", "--network", "--hostname"):
        assert flag in cmd
    assert cmd[cmd.index("--network") + 1] == "netname"
    assert cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert cmd[-1] == "img"


def test_parse_result_reads_json(tmp_path: Path) -> None:
    _ = (tmp_path / "agent-result.json").write_text(
        '{"output":"hi","tool_calls":[{"name":"bash","args":"ls"}],'
        + '"submitted":"FLAG{x}","error":null}',
        encoding="utf-8",
    )
    res = agent._parse_result(tmp_path, subprocess.CompletedProcess([], 0, "", ""))
    assert res.output == "hi"
    assert res.submitted == "FLAG{x}"
    assert res.error is None
    assert [(c.name, c.args) for c in res.tool_calls] == [("bash", "ls")]


def test_parse_result_rejects_non_dict_tool_call_entries(tmp_path: Path) -> None:
    _ = (tmp_path / "agent-result.json").write_text(
        '{"output":"o","tool_calls":["junk",{"name":"n","args":"a"}],'
        + '"submitted":null,"error":null}',
        encoding="utf-8",
    )
    res = agent._parse_result(tmp_path, subprocess.CompletedProcess([], 0, "", ""))
    assert res.output == ""
    assert res.tool_calls == []
    assert res.error is not None
    assert res.error.startswith("malformed agent result")


def test_parse_result_rejects_invalid_json(tmp_path: Path) -> None:
    # A truncated/tampered result file (the workspace is agent-writable) degrades into an error
    # result instead of raising out of the runner.
    _ = (tmp_path / "agent-result.json").write_text('{"output":"tru', encoding="utf-8")
    res = agent._parse_result(tmp_path, subprocess.CompletedProcess([], 0, "", ""))
    assert res.output == ""
    assert res.error is not None
    assert res.error.startswith("malformed agent result")


def test_parse_result_rejects_wrong_typed_field(tmp_path: Path) -> None:
    _ = (tmp_path / "agent-result.json").write_text('{"output":5}', encoding="utf-8")
    res = agent._parse_result(tmp_path, subprocess.CompletedProcess([], 0, "", ""))
    assert res.error is not None
    assert res.error.startswith("malformed agent result")


def test_parse_result_unreadable_file_is_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ = (tmp_path / "agent-result.json").write_text("{}", encoding="utf-8")

    def _raise(_self: Path) -> bytes:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_bytes", _raise)
    res = agent._parse_result(tmp_path, subprocess.CompletedProcess([], 0, "", ""))
    assert res.error is not None
    assert res.error.startswith("malformed agent result")
    assert "OSError" in res.error


def test_parse_result_tolerates_absent_optional_fields(tmp_path: Path) -> None:
    # A result JSON with only `output` (no tool_calls/submitted/error) parses to sensible defaults.
    _ = (tmp_path / "agent-result.json").write_text('{"output":"o"}', encoding="utf-8")
    res = agent._parse_result(tmp_path, subprocess.CompletedProcess([], 0, "", ""))
    assert res.output == "o"
    assert res.tool_calls == []
    assert res.submitted is None
    assert res.error is None


def test_parse_result_missing_file_is_error(tmp_path: Path) -> None:
    res = agent._parse_result(tmp_path, subprocess.CompletedProcess([], 7, "", "boom"))
    assert res.output == ""
    assert res.error is not None
    assert "no result" in res.error
    assert "boom" in res.error


def test_run_agent_container_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    original_mode = tmp_path.stat().st_mode

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "rm" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "No such container")
        _ = (tmp_path / "agent-result.json").write_text(
            '{"output":"done","tool_calls":[],"submitted":null,"error":null}',
            encoding="utf-8",
        )
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake)
    res = run_agent_container(
        image="img", network="net", workspace=tmp_path, config=_cfg(), name="c1"
    )
    cmd = cast("list[str]", seen["cmd"])
    assert res.output == "done"
    assert "img" in cmd
    # The whole config (incl. the key) travels on stdin, never in argv/env.
    assert "secret-key" in str(seen["input"])
    assert "secret-key" not in " ".join(cmd)
    assert tmp_path.stat().st_mode == original_mode


def test_run_agent_container_timeout_force_removes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    removed: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "rm" in cmd:  # the force-remove cleanup call
            removed.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise subprocess.TimeoutExpired(cmd, 1.0)

    monkeypatch.setattr(subprocess, "run", fake)
    res = run_agent_container(
        image="img", network="net", workspace=tmp_path, config=_cfg(timeout=1.0), name="c1"
    )
    assert res.error is not None
    assert "exceeded" in res.error
    assert removed[0] == ["docker", "rm", "-f", "c1"]  # force-remove ran


def test_run_agent_container_removes_stale_outputs_before_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("agent-result.json", "result.txt", "trajectory.json"):
        _ = (tmp_path / name).write_text("stale", encoding="utf-8")

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "rm" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "No such container")
        assert not any((tmp_path / name).exists() for name in agent._RUN_OUTPUTS)
        _ = (tmp_path / "agent-result.json").write_text('{"output":"fresh"}', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake)
    result = run_agent_container(
        image="img", network="net", workspace=tmp_path, config=_cfg(), name="fresh"
    )
    assert result.output == "fresh"


def test_run_agent_container_fails_if_stale_output_cannot_be_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "agent-result.json").mkdir()
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "No such container")

    monkeypatch.setattr(subprocess, "run", fake)
    result = run_agent_container(
        image="img", network="net", workspace=tmp_path, config=_cfg(), name="stale"
    )
    assert result.error is not None
    assert "could not remove stale run output" in result.error
    assert len(calls) == 1  # stale-container cleanup ran, but `docker run` did not


def test_run_agent_container_fails_if_stale_container_cannot_be_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 2, "", "daemon unavailable")

    monkeypatch.setattr(subprocess, "run", fake)
    result = run_agent_container(
        image="img", network="net", workspace=tmp_path, config=_cfg(), name="occupied"
    )
    assert result.error is not None
    assert "could not prepare agent container" in result.error


def test_run_agent_container_reports_run_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = {"count": 0}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["count"] += 1
        if calls["count"] == 1:
            return subprocess.CompletedProcess(cmd, 1, "", "No such container")
        raise OSError("docker disappeared")

    monkeypatch.setattr(subprocess, "run", fake)
    result = run_agent_container(
        image="img", network="net", workspace=tmp_path, config=_cfg(), name="broken"
    )
    assert result.error == "could not run agent container: docker disappeared"


def test_run_agent_container_reports_timeout_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = {"count": 0}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["count"] += 1
        if calls["count"] == 1:
            return subprocess.CompletedProcess(cmd, 1, "", "No such container")
        if calls["count"] == 2:
            raise subprocess.TimeoutExpired(cmd, 1)
        return subprocess.CompletedProcess(cmd, 2, "", "permission denied")

    monkeypatch.setattr(subprocess, "run", fake)
    result = run_agent_container(
        image="img", network="net", workspace=tmp_path, config=_cfg(timeout=1), name="stuck"
    )
    assert result.error is not None
    assert "exceeded" in result.error
    assert "cleanup failed" in result.error


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_nonzero_agent_exit_rejects_valid_result_and_attempts_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cleanup_fails: bool,
) -> None:
    remove_calls = {"count": 0}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "rm" in cmd:
            remove_calls["count"] += 1
            if remove_calls["count"] == 2 and cleanup_fails:
                return subprocess.CompletedProcess(cmd, 2, "", "cleanup denied")
            return subprocess.CompletedProcess(cmd, 1, "", "No such container")
        _ = (tmp_path / "agent-result.json").write_text(
            '{"output":"stale success"}', encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 7, "", "runtime crashed")

    monkeypatch.setattr(subprocess, "run", fake)
    result = run_agent_container(
        image="img", network="net", workspace=tmp_path, config=_cfg(), name="failed"
    )
    assert result.output == ""
    assert result.error is not None
    assert "agent container exited 7: runtime crashed" in result.error
    assert ("cleanup failed" in result.error) is cleanup_fails
    assert remove_calls["count"] == 2


def test_parse_result_nonzero_exit_without_output_detail(tmp_path: Path) -> None:
    _ = (tmp_path / "agent-result.json").write_text('{"output":"ignored"}', encoding="utf-8")
    result = agent._parse_result(tmp_path, subprocess.CompletedProcess([], 9, "", ""))
    assert result.error == "agent container exited 9: no command output"


@pytest.mark.parametrize(
    ("behavior", "expected"),
    [
        ("timeout", "timed out"),
        ("oserror", "could not run"),
        ("failure", "failed: cleanup denied"),
        ("empty-failure", "failed: no command output"),
    ],
)
def test_remove_container_reports_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
    behavior: str,
    expected: str,
) -> None:
    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if behavior == "timeout":
            raise subprocess.TimeoutExpired(cmd, 3)
        if behavior == "oserror":
            raise OSError("docker missing")
        detail = "cleanup denied" if behavior == "failure" else ""
        return subprocess.CompletedProcess(cmd, 2, "", detail)

    monkeypatch.setattr(subprocess, "run", fake)
    error = docker.remove_container("c1", timeout=3)
    assert error is not None
    assert expected in error
