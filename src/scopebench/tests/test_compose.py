"""Hermetic tests for the compose lifecycle (the docker CLI is mocked)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from agent_sandbox import (
    ComposeCleanupError,
    ComposeError,
    ComposeFile,
    ComposeService,
    ComposeStack,
    build_topology,
    find_compose_file,
    read_services,
)
from agent_sandbox.stack import _parse_host_port

_COMPOSE = 'services:\n  app:\n    build: {context: ./c}\n    ports: ["5000:5000"]\n'


def _services(raw: dict[str, object]) -> dict[str, ComposeService]:
    return ComposeFile.model_validate({"services": raw}).services or {}


def _task(tmp_path: Path) -> Path:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    _ = (task_dir / "docker-compose.yaml").write_text(_COMPOSE, encoding="utf-8")
    return task_dir


def _stack(tmp_path: Path) -> ComposeStack:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    topo = build_topology(None, _services({"app": {"ports": ["5000:5000"]}}))
    return ComposeStack(_task(tmp_path), "sb-x", topo, workspace)


def _completed(
    cmd: list[str], rc: int = 0, out: str = "", err: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, rc, out, err)


def test_find_compose_file(tmp_path: Path) -> None:
    assert find_compose_file(_task(tmp_path)).name == "docker-compose.yaml"


def test_find_compose_file_missing(tmp_path: Path) -> None:
    with pytest.raises(ComposeError, match="no compose file"):
        _ = find_compose_file(tmp_path)


def test_read_services(tmp_path: Path) -> None:
    assert set(read_services(_task(tmp_path))) == {"app"}


def test_read_services_rejects_non_mapping(tmp_path: Path) -> None:
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    _ = (task_dir / "compose.yaml").write_text("[]", encoding="utf-8")  # a list, not a mapping
    with pytest.raises(ComposeError, match="invalid compose file"):
        _ = read_services(task_dir)


def test_up_writes_compose_and_resolves_host_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = _stack(tmp_path)

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "port" in cmd:
            return _completed(cmd, out="0.0.0.0:54321\n")
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    assert stack.up() == {"app": {5000: 54321}}
    assert (stack.workspace / ".compose.sb-x.yaml").is_file()
    assert (stack.workspace / "Caddyfile").is_file()


def test_up_raises_on_compose_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stack = _stack(tmp_path)

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(cmd, rc=1, err="up boom")

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(ComposeError, match="up boom"):
        _ = stack.up()


def test_down_tears_down_and_unlinks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _completed(cmd, out="0.0.0.0:1\n") if "port" in cmd else _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    _ = stack.up()
    rewritten = stack.workspace / ".compose.sb-x.yaml"
    assert rewritten.is_file()
    stack.down()
    assert not rewritten.exists()
    assert any("down" in cmd for cmd in calls)


def test_down_is_noop_when_never_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    stack.down()
    assert calls == []


def test_write_run_compose_rejects_non_mapping(tmp_path: Path) -> None:
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    _ = (task_dir / "compose.yaml").write_text("- a\n- b\n", encoding="utf-8")
    workspace = tmp_path / "w"
    workspace.mkdir()
    stack = ComposeStack(task_dir, "p", build_topology(None, {}), workspace)
    with pytest.raises(ComposeError, match="invalid compose file"):
        _ = stack._write_run_compose()


def test_network_name_delegators(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    assert stack.edge_network_name() == "sb-x_edge"
    assert stack.backend_network_name() == "sb-x_backend"


def test_explicit_compose_file_skips_discovery(tmp_path: Path) -> None:
    task_dir = _task(tmp_path)
    compose_file = task_dir / "docker-compose.yaml"
    workspace = tmp_path / "w"
    workspace.mkdir()
    topo = build_topology(None, {})
    stack = ComposeStack(task_dir, "p", topo, workspace, compose_file=compose_file)
    assert stack.compose_file == compose_file


def test_up_skips_port_resolution_for_expose_only_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    _ = (task_dir / "docker-compose.yaml").write_text(
        'services:\n  app:\n    ports: ["5000:5000"]\n  admin:\n    expose: ["9000"]\n',
        encoding="utf-8",
    )
    workspace = tmp_path / "w"
    workspace.mkdir()
    topo = build_topology(
        None, _services({"app": {"ports": ["5000:5000"]}, "admin": {"expose": ["9000"]}})
    )
    stack = ComposeStack(task_dir, "p", topo, workspace)

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(cmd, out="0.0.0.0:1\n") if "port" in cmd else _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    assert set(stack.up()) == {"app"}  # admin has no published ports -> not resolved


def test_write_run_compose_rejects_non_dict_service(tmp_path: Path) -> None:
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    _ = (task_dir / "compose.yaml").write_text("services:\n  x: just-a-string\n", encoding="utf-8")
    workspace = tmp_path / "w"
    workspace.mkdir()
    stack = ComposeStack(
        task_dir, "p", build_topology(None, _services({"app": {"ports": ["1"]}})), workspace
    )
    with pytest.raises(ComposeError, match="invalid compose file"):
        _ = stack._write_run_compose()


def test_write_run_compose_rejects_non_dict_services_block(tmp_path: Path) -> None:
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    _ = (task_dir / "compose.yaml").write_text("services: not-a-mapping\n", encoding="utf-8")
    workspace = tmp_path / "w"
    workspace.mkdir()
    stack = ComposeStack(task_dir, "p", build_topology(None, {}), workspace)
    with pytest.raises(ComposeError, match="invalid compose file"):
        _ = stack._write_run_compose()


def test_write_run_compose_round_trip_fidelity(tmp_path: Path) -> None:
    """The rewritten compose is exactly the original plus the injected slice — nothing invented."""
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    original: dict[str, object] = {
        "version": "3.9",
        "x-custom": "kept",
        "services": {
            "app": {
                "build": {"context": "./c"},
                "environment": {"FOO": None, "BAR": "1"},
                "depends_on": ["db"],
                "ports": ["5000:5000", 8080, {"target": 9090, "published": 90}],
            },
            "admin": {"expose": ["9000"], "networks": {"priv": None}},
            "db": {
                "networks": ["priv"],
                "volumes": [{"type": "volume", "source": "data", "target": "/var/lib/db"}],
            },
        },
        "networks": {"priv": {"driver": "bridge"}},
        "volumes": {"data": None},
    }
    _ = (task_dir / "compose.yaml").write_text(yaml.safe_dump(original), encoding="utf-8")
    workspace = tmp_path / "w"
    workspace.mkdir()
    topo = build_topology(
        None, _services({"app": {"ports": ["5000:5000"]}, "admin": {"expose": ["9000"]}})
    )
    stack = ComposeStack(task_dir, "p", topo, workspace)

    written: object = yaml.safe_load(stack._write_run_compose().read_text(encoding="utf-8")) or {}

    caddyfile = str((workspace / "Caddyfile").resolve())
    expected: dict[str, object] = {
        "version": "3.9",
        "x-custom": "kept",
        "services": {
            "app": {
                "build": {"context": "./c"},
                "environment": {"FOO": None, "BAR": "1"},
                "depends_on": ["db"],
                "ports": ["5000", "8080", "9090"],  # rewritten to bare container ports
                "networks": ["backend"],
            },
            "admin": {"expose": ["9000"], "networks": {"priv": None, "backend": {}}},
            "db": {
                "networks": ["priv", "backend"],
                "volumes": [{"type": "volume", "source": "data", "target": "/var/lib/db"}],
            },
            "_gateway": {
                "image": "caddy:2-alpine",
                "networks": {"backend": {}, "edge": {"aliases": ["app.svc.internal"]}},
                "volumes": [f"{caddyfile}:/etc/caddy/Caddyfile:ro"],
                "healthcheck": {
                    "test": ["CMD", "wget", "-q", "-O", "/dev/null", "http://localhost/healthz"],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 12,
                    "start_period": "2s",
                },
            },
        },
        "networks": {"priv": {"driver": "bridge"}, "backend": {}, "edge": {}},
        "volumes": {"data": None},
    }
    assert written == expected


def test_up_with_unresolvable_host_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _completed(cmd, out="garbage-no-colon") if "port" in cmd else _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(ComposeError, match="could not parse published host port"):
        _ = stack.up()
    assert any("down" in cmd for cmd in calls)  # failed acquisition was rolled back
    assert not (stack.workspace / ".compose.sb-x.yaml").exists()


def test_up_without_services_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    _ = (task_dir / "docker-compose.yaml").write_text("networks: {}\n", encoding="utf-8")
    workspace = tmp_path / "w"
    workspace.mkdir()
    stack = ComposeStack(task_dir, "p", build_topology(None, {}), workspace)

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    assert stack.up() == {}  # no services mapping -> no ports resolved


def test_down_when_rewritten_already_gone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stack = _stack(tmp_path)

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(cmd, out="0.0.0.0:1\n") if "port" in cmd else _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    _ = stack.up()
    (stack.workspace / ".compose.sb-x.yaml").unlink()  # the rewritten file vanishes
    stack.down()  # exercises the "already gone" branch (no unlink attempt)


def test_failed_up_rolls_back_partial_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = _stack(tmp_path)
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if "up" in cmd:
            return _completed(cmd, rc=1, err="service unhealthy")
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(ComposeError, match="service unhealthy"):
        _ = stack.up()
    assert ["up" in cmd for cmd in calls].count(True) == 1
    assert ["down" in cmd for cmd in calls].count(True) == 1
    assert not (stack.workspace / ".compose.sb-x.yaml").exists()


def test_up_failure_preserves_primary_error_when_rollback_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = _stack(tmp_path)

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        action = "down" if "down" in cmd else "up"
        return _completed(cmd, rc=1, err=f"{action} failed")

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(ComposeError, match="up failed") as info:
        _ = stack.up()
    assert any("rollback also failed" in note for note in info.value.__notes__)
    assert (stack.workspace / ".compose.sb-x.yaml").exists()


def test_down_failure_retains_recovery_state_and_can_be_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = _stack(tmp_path)
    fail_down = {"value": True}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "port" in cmd:
            return _completed(cmd, out="0.0.0.0:54321\n")
        if "down" in cmd and fail_down["value"]:
            return _completed(cmd, rc=1, err="daemon refused cleanup")
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    _ = stack.up()
    rewritten = stack.workspace / ".compose.sb-x.yaml"
    with pytest.raises(ComposeCleanupError, match="retry with.*docker compose"):
        stack.down()
    assert rewritten.exists()
    assert stack.port_map == {"app": {5000: 54321}}

    fail_down["value"] = False
    stack.down()
    assert not rewritten.exists()
    assert stack.port_map == {}


def test_running_cleans_success_and_task_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _completed(cmd, out="0.0.0.0:54321\n") if "port" in cmd else _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    first = _stack(tmp_path)
    with first.running() as ports:
        assert ports == {"app": {5000: 54321}}
    assert not (first.workspace / ".compose.sb-x.yaml").exists()

    second_root = tmp_path / "second"
    second_root.mkdir()
    second = _stack(second_root)
    with pytest.raises(RuntimeError, match="task failed"), second.running():
        raise RuntimeError("task failed")
    assert sum("down" in cmd for cmd in calls) == 2


def test_running_cleans_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _completed(cmd, out="0.0.0.0:54321\n") if "port" in cmd else _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    stack = _stack(tmp_path)
    with pytest.raises(KeyboardInterrupt), stack.running():
        raise KeyboardInterrupt
    assert sum("down" in cmd for cmd in calls) == 1


def test_running_keep_retains_success_and_task_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(cmd, out="0.0.0.0:54321\n") if "port" in cmd else _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    first = _stack(tmp_path)
    with first.running(keep=True):
        pass
    assert (first.workspace / ".compose.sb-x.yaml").exists()
    first.down()

    second_root = tmp_path / "second"
    second_root.mkdir()
    second = _stack(second_root)
    with pytest.raises(RuntimeError, match="kept failure"), second.running(keep=True):
        raise RuntimeError("kept failure")
    assert (second.workspace / ".compose.sb-x.yaml").exists()
    second.down()


def test_running_preserves_task_error_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = _stack(tmp_path)
    fail_down = {"value": True}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "port" in cmd:
            return _completed(cmd, out="0.0.0.0:54321\n")
        if "down" in cmd and fail_down["value"]:
            return _completed(cmd, rc=1, err="cleanup broke")
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="primary") as info, stack.running():
        raise RuntimeError("primary")
    assert any("cleanup also failed" in note for note in info.value.__notes__)

    fail_down["value"] = False
    stack.down()


@pytest.mark.parametrize("failure", ["timeout", "oserror"])
def test_docker_up_execution_failure_is_normalized_and_rolled_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    stack = _stack(tmp_path)
    calls = {"count": 0}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["count"] += 1
        if calls["count"] == 1:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(cmd, 1)
            raise OSError("docker missing")
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    message = "timed out" if failure == "timeout" else "could not run"
    with pytest.raises(ComposeError, match=message):
        _ = stack.up()
    assert calls["count"] == 2  # the second command is rollback


def test_down_timeout_is_cleanup_error_with_recovery_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = _stack(tmp_path)

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "port" in cmd:
            return _completed(cmd, out="0.0.0.0:54321\n")
        if "down" in cmd:
            raise subprocess.TimeoutExpired(cmd, 60)
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    _ = stack.up()
    with pytest.raises(ComposeCleanupError, match="timed out after 60s.*retry with"):
        stack.down()


def test_generated_compose_unlink_failure_can_be_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = _stack(tmp_path)

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(cmd, out="0.0.0.0:54321\n") if "port" in cmd else _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    _ = stack.up()
    rewritten = stack.workspace / ".compose.sb-x.yaml"
    real_unlink = Path.unlink

    def fail_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path == rewritten:
            raise OSError("read-only filesystem")
        real_unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as unlink_patch:
        unlink_patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(ComposeCleanupError, match="read-only filesystem"):
            stack.down()
    assert rewritten.exists()
    stack.down()
    assert not rewritten.exists()


def test_repeated_up_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stack = _stack(tmp_path)

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(cmd, out="0.0.0.0:54321\n") if "port" in cmd else _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    _ = stack.up()
    with pytest.raises(ComposeError, match="already been started"):
        _ = stack.up()
    stack.down()


def test_write_run_compose_wraps_generation_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = _stack(tmp_path)
    real_write_text = Path.write_text

    def fail_compose_write(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if path.name.startswith(".compose."):
            raise OSError("workspace became read-only")
        return real_write_text(path, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", fail_compose_write)
    with pytest.raises(ComposeError, match="could not generate compose file.*read-only"):
        _ = stack.up()


def test_up_with_empty_ports_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    _ = (task_dir / "compose.yaml").write_text(
        "services:\n  app:\n    ports: []\n", encoding="utf-8"
    )
    workspace = tmp_path / "w"
    workspace.mkdir()
    services = _services({"app": {"ports": []}})
    stack = ComposeStack(task_dir, "p", build_topology(None, services), workspace)

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake)
    assert stack.up() == {}
    stack.down()


@pytest.mark.parametrize("output", ["host:not-a-port", "host:0", "host:65536"])
def test_parse_host_port_rejects_invalid_port(output: str) -> None:
    with pytest.raises(ComposeError, match="published host port|could not parse"):
        _ = _parse_host_port(output)
