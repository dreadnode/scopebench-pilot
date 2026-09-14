"""Seal a ``agent_harness`` agent in a hardened Docker container on a synthesized realistic network.

The reusable mechanism behind ScopeBench's ``harness`` strategy, decoupled from the benchmark:
manage an isolated :class:`~agent_sandbox.stack.ComposeStack`, synthesize realistic topology,
inject an ``edge``/``backend`` split plus a Caddy gateway, build the agent image, and run the agent
sealed on the edge network (reaching services by realistic FQDN, unable to touch the host or the
internal services).

The in-container entrypoint is the separate :mod:`scopejudge` application. This package's public
surface stays free of the agent library so host callers running only the compose/network side never
import it.
"""

from agent_sandbox.agent import AgentConfig, AgentResult, ToolCall, run_agent_container
from agent_sandbox.compose import (
    ComposeFile,
    ComposeService,
    Healthcheck,
    PortMapping,
    ServiceNetworkAttachment,
    container_port,
)
from agent_sandbox.image import ImageError, ensure_image
from agent_sandbox.network import backend_network_name, edge_network_name, inject_network
from agent_sandbox.stack import (
    COMPOSE_NAMES,
    ComposeCleanupError,
    ComposeError,
    ComposeStack,
    PortMap,
    find_compose_file,
    load_compose,
    read_services,
)
from agent_sandbox.topology import (
    DirectTcpRoute,
    GatewayRoute,
    Host,
    HostRoute,
    HostSpec,
    InternalRoute,
    NetworkSpec,
    RouteBinding,
    Topology,
    TopologyRoutes,
    build_topology,
)

__all__ = [
    "COMPOSE_NAMES",
    "AgentConfig",
    "AgentResult",
    "ComposeCleanupError",
    "ComposeError",
    "ComposeFile",
    "ComposeService",
    "ComposeStack",
    "DirectTcpRoute",
    "GatewayRoute",
    "Healthcheck",
    "Host",
    "HostRoute",
    "HostSpec",
    "ImageError",
    "InternalRoute",
    "NetworkSpec",
    "PortMap",
    "PortMapping",
    "RouteBinding",
    "ServiceNetworkAttachment",
    "ToolCall",
    "Topology",
    "TopologyRoutes",
    "backend_network_name",
    "build_topology",
    "container_port",
    "edge_network_name",
    "ensure_image",
    "find_compose_file",
    "inject_network",
    "load_compose",
    "read_services",
    "run_agent_container",
]
