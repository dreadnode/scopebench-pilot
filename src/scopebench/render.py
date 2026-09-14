"""Instruction rendering and script environments for ScopeBench tasks.

There are three audiences, each with a different view of the same services:

* **The agent** (in its container, on the ``edge`` network) reaches HTTP services through the
  gateway by realistic FQDN on port 80, and raw-TCP services directly at their real port.
  :func:`build_template_context` renders its instruction, so ``{{app_url}}`` becomes e.g.
  ``http://storefront.vesta-market.example`` (HTTP, no ephemeral host port) or
  ``tcp://bastion.acme.example:22`` (raw TCP, with ``{{app_port}}`` carrying the real port).
* **Host-side scripts** (``solution.sh`` / ``dry-run`` verifiers) reach services over their
  ephemeral host port mappings. :func:`build_script_env` builds their ``APP_URL=http://localhost:H``
  environment from the resolved port map, unchanged.
* **A ``where: agent`` verifier** runs in a container on the ``backend`` network and reaches
  services by compose DNS. :func:`build_network_env` builds its ``APP_URL=http://app:5000`` env.

For a service ``app`` we emit the variables ``{{app_url}}``, ``{{app_host}}``, ``{{app_port}}``,
``{{app_url_<port>}}`` (and their ``UPPER_CASE`` env equivalents), matching the platform's names.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_sandbox.topology import Topology

# ``{{ var }}`` with flexible whitespace, matching the platform's mustache-ish syntax.
_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

# service -> {container_port: host_port}
PortMap = dict[str, dict[int, int]]


def _service_entries(port_map: PortMap) -> list[tuple[str, int, int, bool]]:
    """Flatten ``port_map`` into ``(service, container_port, host_port, is_primary)``.

    The first container port declared for a service is its primary port and backs the bare
    ``<service>_url`` / ``<service>_port`` variables.
    """
    entries: list[tuple[str, int, int, bool]] = []
    for service, ports in port_map.items():
        for index, (container_port, host_port) in enumerate(ports.items()):
            entries.append((service, container_port, host_port, index == 0))
    return entries


def build_template_context(topology: Topology) -> dict[str, str]:
    """Build the agent-facing ``{{var}}`` context: FQDN URLs served by the gateway (no port)."""
    context: dict[str, str] = {}
    for host in topology.routes.edge_hosts:
        fqdn = host.route.fqdn
        url = topology.agent_url(host)
        visible_port = str(host.route.visible_port(host.port))
        context[f"{host.service}_url_{host.port}"] = url
        context[f"{host.service}_url"] = url
        context[f"{host.service}_host"] = fqdn
        context[f"{host.service}_port"] = visible_port
    return context


def build_script_env(port_map: PortMap) -> dict[str, str]:
    """Build the ``UPPER_CASE`` env for host-side scripts (solution/dry-run): localhost ports."""
    env: dict[str, str] = {}
    for service, container_port, host_port, is_primary in _service_entries(port_map):
        key = service.upper().replace("-", "_")
        url = f"http://localhost:{host_port}"
        env[f"{key}_URL_{container_port}"] = url
        if is_primary:
            env[f"{key}_URL"] = url
            env[f"{key}_HOST"] = "localhost"
            env[f"{key}_PORT"] = str(host_port)
    return env


def build_network_env(topology: Topology) -> dict[str, str]:
    """Build the ``UPPER_CASE`` env for a ``where: agent`` verifier: service-DNS URLs on backend."""
    env: dict[str, str] = {}
    for host in topology.hosts:
        key = host.service.upper().replace("-", "_")
        url = topology.service_dns_url(host)
        env[f"{key}_URL_{host.port}"] = url
        env[f"{key}_URL"] = url
        env[f"{key}_HOST"] = host.service
        env[f"{key}_PORT"] = str(host.port)
    return env


def render(text: str, context: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute ``{{var}}`` slots in ``text`` from ``context``.

    Args:
        text: The template text (typically a task instruction).
        context: Variable name -> replacement value.

    Returns:
        A ``(rendered, missing)`` tuple. ``missing`` lists any referenced variables that were not
        in ``context`` (their slots are left intact), so callers can treat unresolved variables as
        an error the way smoke does.
    """
    missing: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in context:
            return context[key]
        missing.add(key)
        return match.group(0)

    return _VAR.sub(_replace, text), sorted(missing)
