"""Run an isolated Compose project with the sandbox's edge/backend topology.

The original Compose file remains untouched. Published ports are rewritten to OS-assigned host
ports, the sandbox network and gateway are injected, and generated files live in the per-run
workspace so sibling projects can execute concurrently.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent_sandbox.compose import ComposeFile, ComposeService, PortMapping, container_port
from agent_sandbox.network import backend_network_name, edge_network_name, inject_network
from agent_sandbox.topology import Topology

COMPOSE_NAMES = ("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml")

# service -> {container_port: ephemeral_host_port}
PortMap = dict[str, dict[int, int]]

_PORT_COMMAND_TIMEOUT_SEC = 30.0
_TEARDOWN_TIMEOUT_SEC = 60.0
_MAX_PORT = 65535


class ComposeError(RuntimeError):
    """A Compose file is invalid or a ``docker compose`` command failed."""


class ComposeCleanupError(ComposeError):
    """A Compose project or one of its generated lifecycle files could not be removed."""


def find_compose_file(project_dir: Path) -> Path:
    """Locate a recognized Compose file directly under ``project_dir``."""
    for name in COMPOSE_NAMES:
        candidate = project_dir / name
        if candidate.is_file():
            return candidate
    raise ComposeError(f"no compose file found in {project_dir}")


def load_compose(path: Path) -> ComposeFile:
    """Load and validate ``path``, raising :class:`ComposeError` naming the file."""
    try:
        return ComposeFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, ValidationError, yaml.YAMLError) as exc:
        raise ComposeError(f"invalid compose file {path}: {exc}") from exc


def read_services(project_dir: Path) -> dict[str, ComposeService]:
    """Return the validated Compose ``services:`` mapping under ``project_dir``."""
    compose = load_compose(find_compose_file(project_dir))
    return compose.services if compose.services is not None else {}


@dataclass
class ComposeStack:
    """Manage one isolated sandboxed Compose project."""

    project_dir: Path
    project: str
    topology: Topology
    workspace: Path
    compose_file: Path | None = None
    wait_timeout: int = 180
    port_map: PortMap = field(default_factory=dict)
    _rewritten: Path | None = field(default=None, init=False)
    _start_attempted: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.compose_file is None:
            self.compose_file = find_compose_file(self.project_dir)

    def up(self) -> PortMap:
        """Build and start the project, rolling back every incomplete acquisition."""
        if self._start_attempted:
            raise ComposeError(f"compose project {self.project!r} has already been started")

        try:
            self._rewritten = self._write_run_compose()
            # Compose may create containers and networks before `up --wait` reports a failure.
            # Record the attempt before invoking it so rollback never depends on command success.
            self._start_attempted = True
            _ = self._run(
                "up",
                "-d",
                "--build",
                "--wait",
                "--wait-timeout",
                str(self.wait_timeout),
                check=True,
            )
            self.port_map = self._resolve_ports()
        except BaseException as exc:
            try:
                self.down()
            except ComposeCleanupError as cleanup_exc:
                exc.add_note(f"compose rollback also failed: {cleanup_exc}")
            raise
        return self.port_map

    @contextmanager
    def running(self, *, keep: bool = False) -> Generator[PortMap]:
        """Own one complete stack lifecycle, optionally retaining a successfully started stack.

        Startup rolls itself back on failure. Once acquired, task exceptions remain the primary
        error if cleanup also fails; the cleanup failure is attached as an exception note.
        """
        port_map = self.up()
        try:
            yield port_map
        except BaseException as exc:
            if not keep:
                try:
                    self.down()
                except ComposeCleanupError as cleanup_exc:
                    exc.add_note(f"compose cleanup also failed: {cleanup_exc}")
            raise
        else:
            if not keep:
                self.down()

    def down(self) -> None:
        """Tear down any attempted project, retaining recovery state when cleanup fails."""
        if not self._start_attempted:
            self._remove_generated_compose()
            return
        try:
            proc = self._run(
                "down",
                "-v",
                "--remove-orphans",
                check=False,
                timeout=_TEARDOWN_TIMEOUT_SEC,
            )
        except ComposeError as exc:
            raise ComposeCleanupError(
                f"could not tear down compose project {self.project!r}: {exc}; "
                + f"retry with `{self._recovery_command()}`"
            ) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip() or "no command output"
            raise ComposeCleanupError(
                f"could not tear down compose project {self.project!r}: {detail}; "
                + f"retry with `{self._recovery_command()}`"
            )

        self._start_attempted = False
        self.port_map = {}
        self._remove_generated_compose()

    def edge_network_name(self) -> str:
        """Return the Docker network an edge-only agent should join."""
        return edge_network_name(self.project)

    def backend_network_name(self) -> str:
        """Return the Docker network containing the project services."""
        return backend_network_name(self.project)

    def _base_cmd(self) -> list[str]:
        assert self._rewritten is not None  # noqa: S101 - set before every command
        return [
            "docker",
            "compose",
            "-f",
            str(self._rewritten),
            "--project-directory",
            str(self.project_dir),
            "-p",
            self.project,
        ]

    def _run(
        self,
        *args: str,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed docker CLI, no shell
                [*self._base_cmd(), *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = f"{timeout:.0f}s" if timeout is not None else "its command timeout"
            raise ComposeError(f"`docker compose {args[0]}` timed out after {duration}") from exc
        except OSError as exc:
            raise ComposeError(f"could not run `docker compose {args[0]}`: {exc}") from exc
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip() or "no command output"
            raise ComposeError(f"`docker compose {args[0]}` failed: {detail}")
        return proc

    def _recovery_command(self) -> str:
        """Return a copy-pasteable cleanup command while the generated file is retained."""
        return shlex.join([*self._base_cmd(), "down", "-v", "--remove-orphans"])

    def _remove_generated_compose(self) -> None:
        """Remove the generated Compose file without losing the path after a failed unlink."""
        if self._rewritten is None:
            return
        try:
            self._rewritten.unlink(missing_ok=True)
        except OSError as exc:
            raise ComposeCleanupError(
                f"could not remove generated compose file {self._rewritten}: {exc}"
            ) from exc
        self._rewritten = None

    def _load_compose(self) -> ComposeFile:
        assert self.compose_file is not None  # noqa: S101 - guaranteed by __post_init__
        return load_compose(self.compose_file)

    def _write_run_compose(self) -> Path:
        """Rewrite ports, inject sandbox networking, and persist the generated project file."""
        out = self.workspace / f".compose.{self.project}.yaml"
        # Publish the intended path before generation so a partial write can still be cleaned up.
        self._rewritten = out
        try:
            data = self._load_compose()
            for service in (data.services or {}).values():
                _rewrite_ports(service)
            # Inject after rewriting so the synthesized gateway is never itself port-rewritten.
            inject_network(data, self.topology, self.workspace)
            _ = out.write_text(
                yaml.safe_dump(data.model_dump(mode="python", exclude_unset=True), sort_keys=False),
                encoding="utf-8",
            )
        except ComposeError:
            raise
        except (OSError, yaml.YAMLError) as exc:
            raise ComposeError(f"could not generate compose file {out}: {exc}") from exc
        return out

    def _resolve_ports(self) -> PortMap:
        """Ask Compose for the host port bound to every originally published container port."""
        services = self._load_compose().services
        result: PortMap = {}
        if services is None:
            return result
        for name, service in services.items():
            if service.ports is None:
                continue
            mapping: dict[int, int] = {}
            for entry in service.ports:
                port = container_port(entry)
                proc = self._run(
                    "port",
                    name,
                    str(port),
                    check=True,
                    timeout=_PORT_COMMAND_TIMEOUT_SEC,
                )
                host_port = _parse_host_port(proc.stdout)
                mapping[port] = host_port
            if mapping:
                result[name] = mapping
        return result


def _rewrite_ports(service: ComposeService) -> None:
    """Replace published ports with bare container ports so the OS chooses host ports."""
    if service.ports is not None:
        rewritten: list[str | int | PortMapping] = [
            str(container_port(entry)) for entry in service.ports
        ]
        service.ports = rewritten


def _parse_host_port(port_output: str) -> int:
    """Parse ``docker compose port`` output, rejecting a missing or invalid binding."""
    line = port_output.strip().splitlines()[-1] if port_output.strip() else ""
    if not line or ":" not in line:
        raise ComposeError(f"could not parse published host port from {line!r}")
    try:
        port = int(line.rsplit(":", 1)[-1])
    except ValueError as exc:
        raise ComposeError(f"could not parse published host port from {line!r}") from exc
    if not 1 <= port <= _MAX_PORT:
        raise ComposeError(f"published host port is outside 1..{_MAX_PORT}: {port}")
    return port
