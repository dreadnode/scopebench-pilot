"""The network topology synthesized for a task: which hosts the agent sees, at what FQDN.

Derived from the task's compose file plus an optional ``network:`` block, this is the single
source of truth for three consumers:

* :mod:`agent_sandbox.network` — the edge/backend network split and the gateway's vhosts/aliases.
* the benchmark's instruction rendering — the realistic FQDN URLs handed to the agent.
* caller-defined prompt rendering and policy signals.

The published/internal split is read straight from the compose file: a service with a ``ports:``
key is agent-facing (``edge``); HTTP services get gateway vhosts and raw-TCP services join the
edge network directly. A service with only ``expose:`` is internal and stays agent-unreachable.
**Scope is never encoded here** — callers decide which services are authorized.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field

from agent_sandbox.compose import ComposeService, container_port

_DEFAULT_DOMAIN = "svc.internal"


class _NetworkSpecModel(BaseModel):
    """Base for the task.yaml ``network:`` models: our own spec, so unknown keys are rejected."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class HostSpec(_NetworkSpecModel):
    """Agent-facing naming and transport for one service."""

    fqdn: str | None = Field(
        default=None, description="Exact FQDN, used verbatim (wins over subdomain/domain)."
    )
    domain: str | None = Field(
        default=None, description="Per-host base domain (falls back to network.domain)."
    )
    subdomain: str | None = Field(
        default=None, description="Leftmost label (falls back to the service name)."
    )
    aliases: tuple[str, ...] = Field(
        default=(),
        description=(
            "Additional DNS names for the service. On a published host they are extra "
            "agent-facing FQDNs (gateway vhosts, or direct names for a raw-TCP host); on an "
            "expose-only internal host they are the backend names an in-scope foothold reaches "
            "it by (the host stays agent-unreachable). ``fqdn`` is not applied to internal "
            "hosts -- give a pivot target a realistic internal name via ``aliases``."
        ),
    )
    transport: Literal["http", "tcp"] = Field(
        default="http",
        description=(
            "HTTP services use the synthesized reverse proxy; TCP services are attached "
            "directly to the agent-facing network."
        ),
    )


class NetworkSpec(_NetworkSpecModel):
    """The optional ``network:`` block of a task.yaml: the task's realistic-DNS story."""

    domain: str = Field(default=_DEFAULT_DOMAIN, description="Base domain for derived host FQDNs.")
    hosts: dict[str, HostSpec] = Field(
        default_factory=dict, description="Per-service FQDN overrides, keyed by service name."
    )


@dataclass(frozen=True)
class GatewayRoute:
    """A published HTTP service reached through the synthesized gateway."""

    fqdn: str
    aliases: tuple[str, ...] = ()
    kind: Literal["gateway"] = field(default="gateway", init=False)

    edge: ClassVar[Literal[True]] = True
    transport: ClassVar[Literal["http"]] = "http"

    @property
    def compatibility_aliases(self) -> tuple[str, ...]:
        """Return the aliases exposed through the historical :class:`Host` facade."""
        return self.aliases

    @property
    def names(self) -> tuple[str, ...]:
        """Return every agent-facing gateway name, canonical name first."""
        return (self.fqdn, *self.aliases)

    def agent_url(self, _container_port: int) -> str:
        """Return the endpoint presented to the agent through the HTTP gateway."""
        return f"http://{self.fqdn}"

    def visible_port(self, _container_port: int) -> int:
        """Return the gateway port presented to the agent."""
        return 80


@dataclass(frozen=True)
class DirectTcpRoute:
    """A published raw-TCP service attached directly to the edge network."""

    fqdn: str
    aliases: tuple[str, ...] = ()
    kind: Literal["direct_tcp"] = field(default="direct_tcp", init=False)

    edge: ClassVar[Literal[True]] = True
    transport: ClassVar[Literal["tcp"]] = "tcp"

    @property
    def compatibility_aliases(self) -> tuple[str, ...]:
        """Return the aliases exposed through the historical :class:`Host` facade."""
        return self.aliases

    @property
    def names(self) -> tuple[str, ...]:
        """Return every direct edge name, canonical name first."""
        return (self.fqdn, *self.aliases)

    def agent_url(self, container_port: int) -> str:
        """Return the raw-TCP endpoint presented directly to the agent."""
        return f"tcp://{self.fqdn}:{container_port}"

    def visible_port(self, container_port: int) -> int:
        """Return the container port exposed directly to the agent."""
        return container_port


@dataclass(frozen=True)
class InternalRoute:
    """An expose-only service that remains reachable only on the backend network."""

    fqdn: str
    backend_aliases: tuple[str, ...] = ()
    kind: Literal["internal"] = field(default="internal", init=False)

    edge: ClassVar[Literal[False]] = False
    transport: ClassVar[Literal["http"]] = "http"

    @property
    def compatibility_aliases(self) -> tuple[str, ...]:
        """Return backend aliases through the historical :class:`Host` facade."""
        return self.backend_aliases

    def agent_url(self, _container_port: int) -> str:
        """Return the legacy policy-identity URL for compatibility."""
        return f"http://{self.fqdn}"

    def visible_port(self, _container_port: int) -> int:
        """Return the conventional HTTP port for the legacy compatibility view."""
        return 80


type HostRoute = GatewayRoute | DirectTcpRoute | InternalRoute


@dataclass(frozen=True, init=False)
class Host:
    """One service and its explicit route through the synthesized topology.

    The historical constructor and ``fqdn``/``edge``/``aliases``/``transport`` attributes remain
    available as a compatibility facade. New code should construct hosts through
    :meth:`gateway`, :meth:`direct_tcp`, or :meth:`internal` and inspect :attr:`route`.
    """

    service: str
    port: int
    route: HostRoute

    def __init__(
        self,
        service: str,
        fqdn: str,
        edge: bool,
        port: int,
        aliases: tuple[str, ...] = (),
        transport: Literal["http", "tcp"] = "http",
    ) -> None:
        """Construct a host through the pre-route public signature."""
        if edge:
            route: HostRoute = (
                DirectTcpRoute(fqdn=fqdn, aliases=aliases)
                if transport == "tcp"
                else GatewayRoute(fqdn=fqdn, aliases=aliases)
            )
        else:
            if transport == "tcp":
                raise ValueError("an internal host cannot use direct TCP transport")
            route = InternalRoute(fqdn=fqdn, backend_aliases=aliases)
        object.__setattr__(self, "service", service)
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "route", route)

    @classmethod
    def _from_route(cls, service: str, port: int, route: HostRoute) -> Host:
        host = object.__new__(cls)
        object.__setattr__(host, "service", service)
        object.__setattr__(host, "port", port)
        object.__setattr__(host, "route", route)
        return host

    @classmethod
    def gateway(cls, service: str, fqdn: str, port: int, aliases: tuple[str, ...] = ()) -> Host:
        """Construct a published HTTP host."""
        return cls._from_route(service, port, GatewayRoute(fqdn=fqdn, aliases=aliases))

    @classmethod
    def direct_tcp(cls, service: str, fqdn: str, port: int, aliases: tuple[str, ...] = ()) -> Host:
        """Construct a published raw-TCP host."""
        return cls._from_route(service, port, DirectTcpRoute(fqdn=fqdn, aliases=aliases))

    @classmethod
    def internal(
        cls, service: str, fqdn: str, port: int, backend_aliases: tuple[str, ...] = ()
    ) -> Host:
        """Construct an expose-only backend host."""
        return cls._from_route(
            service,
            port,
            InternalRoute(fqdn=fqdn, backend_aliases=backend_aliases),
        )

    @property
    def fqdn(self) -> str:
        """Return the canonical task hostname retained by every route variant."""
        return self.route.fqdn

    @property
    def edge(self) -> bool:
        """Whether this host is agent-facing."""
        return self.route.edge

    @property
    def aliases(self) -> tuple[str, ...]:
        """Return this route's edge aliases or backend aliases, for compatibility."""
        return self.route.compatibility_aliases

    @property
    def transport(self) -> Literal["http", "tcp"]:
        """Return the legacy transport label derived from the route."""
        return self.route.transport


@dataclass(frozen=True)
class RouteBinding[RouteT: HostRoute]:
    """A host paired with the route variant already narrowed for a consumer."""

    host: Host
    route: RouteT


@dataclass(frozen=True)
class TopologyRoutes:
    """One exhaustive, typed index over the hosts in a :class:`Topology`."""

    edge_hosts: tuple[Host, ...]
    gateway: tuple[RouteBinding[GatewayRoute], ...]
    direct_tcp: tuple[RouteBinding[DirectTcpRoute], ...]
    internal: tuple[RouteBinding[InternalRoute], ...]

    @classmethod
    def from_hosts(cls, hosts: tuple[Host, ...]) -> TopologyRoutes:
        """Partition hosts once while preserving their declaration order."""
        edge_hosts: list[Host] = []
        gateway: list[RouteBinding[GatewayRoute]] = []
        direct_tcp: list[RouteBinding[DirectTcpRoute]] = []
        internal: list[RouteBinding[InternalRoute]] = []
        for host in hosts:
            route = host.route
            match route:
                case GatewayRoute() as route:
                    edge_hosts.append(host)
                    gateway.append(RouteBinding(host=host, route=route))
                    continue
                case DirectTcpRoute() as route:
                    edge_hosts.append(host)
                    direct_tcp.append(RouteBinding(host=host, route=route))
                    continue
                case InternalRoute() as route:
                    internal.append(RouteBinding(host=host, route=route))
                    continue
            assert_never(route)  # pragma: no cover - HostRoute is exhaustively matched
        return cls(
            edge_hosts=tuple(edge_hosts),
            gateway=tuple(gateway),
            direct_tcp=tuple(direct_tcp),
            internal=tuple(internal),
        )


@dataclass(frozen=True)
class Topology:
    """The synthesized per-task network the agent sees.

    Attributes:
        hosts: Every service that exposes a port (published or ``expose``-only).
        domain: The base domain used to derive host FQDNs.
    """

    hosts: tuple[Host, ...]
    domain: str
    routes: TopologyRoutes = field(init=False)

    def __post_init__(self) -> None:
        """Build the typed route index for all downstream consumers."""
        object.__setattr__(self, "routes", TopologyRoutes.from_hosts(self.hosts))

    def edge_hosts(self) -> tuple[Host, ...]:
        """Return every agent-facing service declared with compose ``ports``."""
        return self.routes.edge_hosts

    def gateway_hosts(self) -> tuple[Host, ...]:
        """Published HTTP hosts served through the synthesized gateway."""
        return tuple(binding.host for binding in self.routes.gateway)

    def direct_edge_hosts(self) -> tuple[Host, ...]:
        """Published raw-TCP hosts attached directly to the agent-facing network."""
        return tuple(binding.host for binding in self.routes.direct_tcp)

    def internal_alias_hosts(self) -> tuple[Host, ...]:
        """Internal (``expose``-only) hosts that declared backend DNS aliases.

        These stay agent-unreachable -- the alias is applied on ``backend``, which the agent never
        joins -- but let an in-scope foothold reach the host by a realistic name (e.g. an
        SSRF/pivot target addressed as ``vault.corp.internal`` rather than by bare service name).
        """
        return tuple(
            binding.host for binding in self.routes.internal if binding.route.backend_aliases
        )

    def agent_url(self, host: Host) -> str:
        """Return the agent-facing endpoint for an HTTP or raw-TCP host."""
        return host.route.agent_url(host.port)

    def service_dns_url(self, host: Host) -> str:
        """The in-network service-DNS URL (for a ``where: agent`` verifier on the backend net)."""
        return f"http://{host.service}:{host.port}"


def _host_fqdn(service: str, overrides: dict[str, HostSpec], domain: str) -> str:
    """Resolve a service's FQDN from the optional ``network.hosts`` overrides.

    ``fqdn`` wins verbatim; otherwise ``subdomain``/``domain`` are joined, falling back to
    ``<service>.<domain>`` when the service is not listed.
    """
    spec = overrides.get(service)
    if spec is None:
        return f"{service}.{domain}"
    if spec.fqdn is not None:
        return spec.fqdn
    base = spec.domain if spec.domain is not None else domain
    label = spec.subdomain if spec.subdomain is not None else service
    return f"{label}.{base}"


def build_topology(network: NetworkSpec | None, services: Mapping[str, ComposeService]) -> Topology:
    """Build the :class:`Topology` from an optional ``network:`` spec and the compose services.

    Args:
        network: The task's parsed ``network:`` block, or ``None`` for all defaults.
        services: The compose file's validated ``services:`` mapping.

    A service with compose ``ports`` is agent-facing (``edge``); a service with only ``expose``
    is internal. Services with neither — an explicit ``ports: null`` counts as absent — are
    omitted (they are not reachable and are never referenced in an instruction).
    """
    spec = network if network is not None else NetworkSpec()
    hosts: list[Host] = []
    for name, service in services.items():
        published = service.ports is not None
        port_source = service.ports if published else service.expose
        if not port_source:
            continue
        host_spec = spec.hosts.get(name)
        fqdn = _host_fqdn(name, spec.hosts, spec.domain)
        port = container_port(port_source[0])
        aliases = host_spec.aliases if host_spec is not None else ()
        transport = host_spec.transport if host_spec is not None else "http"
        if not published:
            if transport == "tcp":
                raise ValueError(f"internal service {name!r} cannot use direct TCP transport")
            host = Host.internal(name, fqdn, port, backend_aliases=aliases)
        elif transport == "tcp":
            host = Host.direct_tcp(name, fqdn, port, aliases=aliases)
        else:
            host = Host.gateway(name, fqdn, port, aliases=aliases)
        hosts.append(host)
    return Topology(hosts=tuple(hosts), domain=spec.domain)
