"""Synthesize the agent's network into a task's compose file.

Given a validated :class:`~agent_sandbox.compose.ComposeFile` and a
:class:`~agent_sandbox.topology.Topology`, this adds two bridge networks — ``backend`` (every task
service) and ``edge`` (the agent-facing network). A dual-homed Caddy service reverse-proxies
published HTTP FQDNs; declared raw-TCP services join ``edge`` directly. All task services join
``backend`` and the agent joins only ``edge``, so ``expose``-only services remain unreachable —
the isolation the SSRF and pivot tasks depend on. An ``expose``-only service may still declare
backend DNS aliases, letting an in-scope foothold reach it by a realistic name (e.g. a pivot
target addressed as ``vault.corp.internal``) while it stays agent-unreachable.

The network *names* Docker creates (``<project>_edge`` / ``<project>_backend``) are single-sourced
here alongside the keys used to *create* them, so a caller and the synthesis never drift apart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_sandbox.compose import (
    ComposeFile,
    ComposeService,
    Healthcheck,
    ServiceNetworkAttachment,
)
from agent_sandbox.gateway import render_caddyfile

if TYPE_CHECKING:
    from pathlib import Path

    from agent_sandbox.topology import Topology

_GATEWAY_SERVICE = "_gateway"
_BACKEND_NET = "backend"
_EDGE_NET = "edge"
_CADDYFILE_NAME = "Caddyfile"


def edge_network_name(project: str) -> str:
    """The Docker network the agent container joins (``<project>_edge``)."""
    return f"{project}_{_EDGE_NET}"


def backend_network_name(project: str) -> str:
    """The Docker network task services live on (``<project>_backend``)."""
    return f"{project}_{_BACKEND_NET}"


def inject_network(compose: ComposeFile, topology: Topology, workspace: Path) -> None:
    """Add the edge/backend networks + a Caddy gateway to ``compose``; write the Caddyfile.

    Mutates ``compose`` in place. The gateway is added *after* the services are attached to
    ``backend`` (so it is never itself rewritten by an earlier port-rewrite pass), and the Caddyfile
    it mounts is written into ``workspace``.
    """
    if compose.services is not None:
        for service in compose.services.values():
            service.attach_network(_BACKEND_NET)
        for binding in topology.routes.direct_tcp:
            host = binding.host
            service = compose.services.get(host.service)
            if service is None:
                continue
            service.attach_network(_EDGE_NET, binding.route.names)
        for binding in topology.routes.internal:
            if not binding.route.backend_aliases:
                continue
            service = compose.services.get(binding.host.service)
            if service is not None:
                # The host stays off ``edge`` while an in-scope foothold can use these names.
                service.attach_network(_BACKEND_NET, binding.route.backend_aliases)
        compose.services[_GATEWAY_SERVICE] = _gateway_service(topology, workspace)
    _add_networks(compose)
    caddyfile = workspace / _CADDYFILE_NAME
    _ = caddyfile.write_text(render_caddyfile(topology), encoding="utf-8")


def _gateway_service(topology: Topology, workspace: Path) -> ComposeService:
    """The synthesized Caddy gateway: edge FQDN aliases -> backend service ports."""
    aliases = [name for binding in topology.routes.gateway for name in binding.route.names]
    caddyfile = str((workspace / _CADDYFILE_NAME).resolve())
    return ComposeService(
        image="caddy:2-alpine",
        networks={
            _BACKEND_NET: ServiceNetworkAttachment(),
            _EDGE_NET: ServiceNetworkAttachment(aliases=aliases),
        },
        volumes=[f"{caddyfile}:/etc/caddy/Caddyfile:ro"],
        healthcheck=Healthcheck(
            test=["CMD", "wget", "-q", "-O", "/dev/null", "http://localhost/healthz"],
            interval="5s",
            timeout="3s",
            retries=12,
            start_period="2s",
        ),
    )


def _add_networks(compose: ComposeFile) -> None:
    """Add the ``backend`` and ``edge`` bridge networks to the top-level ``networks`` block."""
    networks = compose.networks if compose.networks is not None else {}
    _ = networks.setdefault(_BACKEND_NET, {})
    _ = networks.setdefault(_EDGE_NET, {})
    compose.networks = networks
