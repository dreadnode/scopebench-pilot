"""Hermetic tests for the where:agent container verifier + flag-path helpers (docker mocked)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import cast

import pytest

from scopebench.manifest import FlagVerification, ScriptVerification
from scopebench.verify import (
    _DEFAULT_FLAG_PATHS,
    VerificationError,
    collect,
    flag_paths,
    run_verifier,
    run_verifier_container,
)
from scopejudge.runtime import _FLAG_SRC


def test_flag_paths_includes_verification_path_and_dedups(tmp_path: Path) -> None:
    # verification.path duplicates the default /tmp/result.txt -> deduped, order preserved.
    paths = flag_paths(
        FlagVerification(method="flag", hash="x", path="/tmp/result.txt"),
        tmp_path,
        include_shared_tmp=True,
    )
    assert tmp_path / "result.txt" in paths
    assert Path("/tmp/result.txt") in paths
    assert len(paths) == len(set(paths))


def test_script_verification_requires_script() -> None:
    with pytest.raises(ValueError, match="Field required"):
        _ = ScriptVerification.model_validate({"method": "script"})


def test_run_verifier_container_script_not_found(tmp_path: Path) -> None:
    out = run_verifier_container(
        ScriptVerification(method="script", script="nope.sh"), tmp_path, tmp_path, {}, "net"
    )
    assert out.verified is False
    assert "not found" in out.detail


def test_run_verifier_container_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _ = (tmp_path / "v.sh").write_text("exit 0\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(cmd, 0, "PASS", "")

    monkeypatch.setattr(subprocess, "run", fake)
    out = run_verifier_container(
        ScriptVerification(method="script", script="v.sh"),
        tmp_path,
        tmp_path,
        {"APP_URL": "http://app:5000"},
        "netname",
    )
    cmd = cast("list[str]", seen["cmd"])
    assert out.verified is True
    assert "netname" in cmd
    assert cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert "-e" in cmd  # the service-DNS env is injected
    assert "exit 0" in str(seen["input"])  # the verifier source travels on stdin


def test_run_verifier_container_timeout_force_removes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ = (tmp_path / "v.sh").write_text("sleep 100\n", encoding="utf-8")
    removed: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "rm" in cmd:
            removed.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(VerificationError, match="timed out"):
        _ = run_verifier_container(
            ScriptVerification(method="script", script="v.sh", timeout=30),
            tmp_path,
            tmp_path,
            {},
            "net",
        )
    assert removed


@pytest.mark.parametrize("failure", ["timeout", "oserror"])
def test_host_verifier_execution_failures_are_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    _ = (tmp_path / "v.sh").write_text("exit 0\n", encoding="utf-8")

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 30)
        raise OSError("bash missing")

    monkeypatch.setattr(subprocess, "run", fake)
    expected = "timed out" if failure == "timeout" else "could not run verifier"
    with pytest.raises(VerificationError, match=expected):
        _ = run_verifier(
            ScriptVerification(method="script", script="v.sh", timeout=30),
            tmp_path,
            tmp_path,
            {},
        )


def test_verifier_container_prepare_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ = (tmp_path / "v.sh").write_text("exit 0\n", encoding="utf-8")

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 2, "", "daemon unavailable")

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(VerificationError, match="could not prepare verifier container"):
        _ = run_verifier_container(
            ScriptVerification(method="script", script="v.sh"), tmp_path, tmp_path, {}, "net"
        )


def test_verifier_container_run_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _ = (tmp_path / "v.sh").write_text("exit 0\n", encoding="utf-8")
    calls = {"count": 0}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["count"] += 1
        if calls["count"] == 1:
            return subprocess.CompletedProcess(cmd, 1, "", "No such container")
        raise OSError("docker missing")

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(VerificationError, match="could not run verifier container"):
        _ = run_verifier_container(
            ScriptVerification(method="script", script="v.sh"), tmp_path, tmp_path, {}, "net"
        )


def test_verifier_timeout_reports_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ = (tmp_path / "v.sh").write_text("sleep 100\n", encoding="utf-8")
    calls = {"count": 0}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["count"] += 1
        if calls["count"] == 1:
            return subprocess.CompletedProcess(cmd, 1, "", "No such container")
        if calls["count"] == 2:
            raise subprocess.TimeoutExpired(cmd, 30)
        return subprocess.CompletedProcess(cmd, 2, "", "cleanup denied")

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(VerificationError, match="timed out.*cleanup failed"):
        _ = run_verifier_container(
            ScriptVerification(method="script", script="v.sh", timeout=30),
            tmp_path,
            tmp_path,
            {},
            "net",
        )


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_nonzero_verifier_exit_attempts_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cleanup_fails: bool,
) -> None:
    _ = (tmp_path / "v.sh").write_text("exit 3\n", encoding="utf-8")
    remove_calls = {"count": 0}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "rm" in cmd:
            remove_calls["count"] += 1
            if remove_calls["count"] == 2 and cleanup_fails:
                return subprocess.CompletedProcess(cmd, 2, "", "cleanup denied")
            return subprocess.CompletedProcess(cmd, 1, "", "No such container")
        return subprocess.CompletedProcess(cmd, 3, "", "verification failed")

    monkeypatch.setattr(subprocess, "run", fake)
    if cleanup_fails:
        with pytest.raises(VerificationError, match="verifier exit 3.*cleanup failed"):
            _ = run_verifier_container(
                ScriptVerification(method="script", script="v.sh"),
                tmp_path,
                tmp_path,
                {},
                "net",
            )
    else:
        outcome = run_verifier_container(
            ScriptVerification(method="script", script="v.sh"), tmp_path, tmp_path, {}, "net"
        )
        assert outcome.verified is False
        assert "verifier exit 3" in outcome.detail


def test_collect_uses_container_verifier_for_where_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ = (tmp_path / "v.sh").write_text("exit 0\n", encoding="utf-8")

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, "PASS", "")

    monkeypatch.setattr(subprocess, "run", fake)
    out = collect(
        ScriptVerification(method="script", script="v.sh", where="agent"),
        tmp_path,
        tmp_path,
        {},
        {"APP_URL": "http://app:5000"},
        "net",
        agent_in_container=True,
    )
    assert out.verified is True


def test_default_flag_path_matches_in_container_driver() -> None:
    # The benchmark's host-side default and ScopeJudge's _FLAG_SRC are one task
    # contract in two processes; deliberately not code-shared (the runtime ships into the
    # image alone), so this parity test is the drift alarm.
    assert _DEFAULT_FLAG_PATHS[0] == str(_FLAG_SRC)
