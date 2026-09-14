"""Hermetic tests for agent_sandbox topology, gateway, and network synthesis.

No Docker: these exercise the pure logic that derives the network/gateway from a compose file and
builds the isolated topology. The live container path is validated by running the ScopeBench CLI
against real stacks (see src/scopebench/README.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_sandbox import (
    ComposeFile,
    ComposeService,
    DirectTcpRoute,
    GatewayRoute,
    Host,
    InternalRoute,
    NetworkSpec,
    PortMapping,
    Topology,
    TopologyRoutes,
    backend_network_name,
    build_topology,
    container_port,
    edge_network_name,
    inject_network,
)
from agent_sandbox.gateway import render_caddyfile


def _services(raw: dict[str, object]) -> dict[str, ComposeService]:
    """Validate raw service definitions through the production compose model."""
    return ComposeFile.model_validate({"services": raw}).services or {}


# --- topology --------------------------------------------------------------


def test_build_topology_splits_edge_and_internal() -> None:
    services = _services(
        {
            "app": {"ports": ["5000:5000"]},
            "admin": {"expose": ["9000"]},  # internal target — reachable only via SSRF
            "initdb": {"image": "alpine"},  # no ports/expose -> omitted from the topology
        }
    )
    topo = build_topology(None, services)
    by_service = {host.service: host for host in topo.hosts}
    assert set(by_service) == {"app", "admin"}
    assert by_service["app"].edge is True
    assert by_service["admin"].edge is False
    assert by_service["app"].fqdn == "app.svc.internal"  # zero-config default domain
    assert [host.service for host in topo.edge_hosts()] == ["app"]


def test_build_topology_applies_network_overrides() -> None:
    services = _services({"app": {"ports": ["5000:5000"]}, "metadata": {"expose": ["8080"]}})
    network = NetworkSpec.model_validate(
        {
            "domain": "vesta-market.example",
            "hosts": {
                "app": {"subdomain": "storefront"},
                "metadata": {"fqdn": "metadata.internal"},
            },
        }
    )
    topo = build_topology(network, services)
    by_service = {host.service: host for host in topo.hosts}
    assert by_service["app"].fqdn == "storefront.vesta-market.example"
    assert by_service["metadata"].fqdn == "metadata.internal"


def test_build_topology_applies_aliases_and_tcp_transport() -> None:
    services = _services({"server": {"ports": ["22"]}, "app": {"ports": ["80"]}})
    network = NetworkSpec.model_validate(
        {
            "hosts": {
                "server": {
                    "fqdn": "bastion.example",
                    "aliases": ["ssh.example"],
                    "transport": "tcp",
                }
            }
        }
    )
    topo = build_topology(network, services)
    server = topo.hosts[0]
    assert isinstance(server.route, DirectTcpRoute)
    assert server.aliases == ("ssh.example",)
    assert server.transport == "tcp"
    assert topo.direct_edge_hosts() == (server,)
    assert [host.service for host in topo.gateway_hosts()] == ["app"]


def test_internal_alias_hosts_surface_declared_backend_aliases() -> None:
    services = _services({"app": {"ports": ["5000"]}, "vault": {"expose": ["6000"]}})
    network = NetworkSpec.model_validate({"hosts": {"vault": {"aliases": ["vault.corp.internal"]}}})
    topo = build_topology(network, services)
    vault = {host.service: host for host in topo.hosts}["vault"]
    assert isinstance(vault.route, InternalRoute)
    assert vault.edge is False  # stays internal / agent-unreachable
    assert vault.aliases == ("vault.corp.internal",)
    assert topo.internal_alias_hosts() == (vault,)
    # an internal host that declared no aliases is not surfaced
    plain = build_topology(None, _services({"app": {"ports": ["1"]}, "x": {"expose": ["2"]}}))
    assert plain.internal_alias_hosts() == ()


def test_host_route_constructors_preserve_compatibility_properties() -> None:
    gateway = Host.gateway("app", "app.example", 80, aliases=("www.example",))
    direct = Host.direct_tcp("ssh", "ssh.example", 22, aliases=("bastion.example",))
    internal = Host.internal(
        "vault",
        "vault.policy.internal",
        8200,
        backend_aliases=("vault.backend.internal",),
    )

    assert isinstance(gateway.route, GatewayRoute)
    assert (gateway.fqdn, gateway.edge, gateway.transport, gateway.aliases) == (
        "app.example",
        True,
        "http",
        ("www.example",),
    )
    assert gateway.route.names == ("app.example", "www.example")
    assert gateway.route.visible_port(gateway.port) == 80
    assert isinstance(direct.route, DirectTcpRoute)
    assert (direct.edge, direct.transport, direct.aliases) == (
        True,
        "tcp",
        ("bastion.example",),
    )
    assert direct.route.names == ("ssh.example", "bastion.example")
    assert direct.route.visible_port(direct.port) == 22
    assert isinstance(internal.route, InternalRoute)
    assert (internal.fqdn, internal.edge, internal.transport, internal.aliases) == (
        "vault.policy.internal",
        False,
        "http",
        ("vault.backend.internal",),
    )
    assert internal.route.visible_port(internal.port) == 80


def test_topology_builds_ordered_typed_route_bindings() -> None:
    gateway = Host.gateway("app", "app.example", 80)
    internal = Host.internal("vault", "vault.internal", 8200)
    direct = Host.direct_tcp("ssh", "ssh.example", 22)

    topology = Topology(hosts=(gateway, internal, direct), domain="example")

    assert isinstance(topology.routes, TopologyRoutes)
    assert topology.routes.edge_hosts == (gateway, direct)
    assert [(binding.host, binding.route) for binding in topology.routes.gateway] == [
        (gateway, gateway.route)
    ]
    assert [(binding.host, binding.route) for binding in topology.routes.internal] == [
        (internal, internal.route)
    ]
    assert [(binding.host, binding.route) for binding in topology.routes.direct_tcp] == [
        (direct, direct.route)
    ]


def test_legacy_host_constructor_rejects_internal_tcp() -> None:
    with pytest.raises(ValueError, match="internal host cannot use direct TCP"):
        _ = Host("vault", "vault.internal", edge=False, port=22, transport="tcp")


def test_build_topology_rejects_internal_tcp_transport() -> None:
    services = _services({"vault": {"expose": [22]}})
    network = NetworkSpec.model_validate({"hosts": {"vault": {"transport": "tcp"}}})
    with pytest.raises(ValueError, match="internal service 'vault'"):
        _ = build_topology(network, services)


def test_inject_network_attaches_internal_alias_to_backend(tmp_path: Path) -> None:
    raw: dict[str, object] = {"app": {"ports": ["5000"]}, "vault": {"expose": ["6000"]}}
    compose = ComposeFile.model_validate({"services": raw})
    topology = build_topology(
        NetworkSpec.model_validate({"hosts": {"vault": {"aliases": ["vault.corp.internal"]}}}),
        _services(raw),
    )
    inject_network(compose, topology, tmp_path)

    vault_networks = (compose.services or {})["vault"].networks
    assert isinstance(vault_networks, dict)
    # internal host stays off `edge` (agent-unreachable) but carries the alias on `backend`
    assert set(vault_networks) == {"backend"}
    backend = vault_networks["backend"]
    assert backend is not None
    assert backend.aliases == ["vault.corp.internal"]


def test_inject_network_internal_alias_preserves_existing_networks(tmp_path: Path) -> None:
    # An internal host that already declares its own compose network keeps it and gains the
    # backend alias (the mapping-shaped attachment path).
    raw: dict[str, object] = {
        "app": {"ports": ["5000"]},
        "vault": {"expose": ["6000"], "networks": {"secret": None}},
    }
    compose = ComposeFile.model_validate({"services": raw})
    topology = build_topology(
        NetworkSpec.model_validate({"hosts": {"vault": {"aliases": ["vault.corp.internal"]}}}),
        _services(raw),
    )
    inject_network(compose, topology, tmp_path)

    vault_networks = (compose.services or {})["vault"].networks
    assert isinstance(vault_networks, dict)
    assert set(vault_networks) == {"secret", "backend"}  # keeps its own net, gains backend
    backend = vault_networks["backend"]
    assert backend is not None
    assert backend.aliases == ["vault.corp.internal"]


def test_network_spec_rejects_unknown_keys() -> None:
    # The network block is this project's own spec, so a typo fails loudly at parse time.
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _ = NetworkSpec.model_validate({"domain": "x", "bogus": "y"})


def test_build_topology_treats_null_ports_as_absent() -> None:
    # `ports: null` used to crash the port rewrite; it now means "no published ports".
    services = _services({"app": {"ports": None, "expose": ["8080"]}})
    topo = build_topology(None, services)
    assert topo.hosts[0].edge is False


def test_host_fqdn_domain_override_without_subdomain() -> None:
    network = NetworkSpec.model_validate({"hosts": {"app": {"domain": "corp.internal"}}})
    topo = build_topology(network, _services({"app": {"ports": ["1"]}}))
    assert topo.hosts[0].fqdn == "app.corp.internal"  # label=service, base=per-host domain


def test_agent_url_and_service_dns_url() -> None:
    host = Host("app", "storefront.example", edge=True, port=5000)
    topo = Topology(hosts=(host,), domain="example")
    assert topo.agent_url(host) == "http://storefront.example"  # gateway, no port
    assert topo.service_dns_url(host) == "http://app:5000"  # backend service DNS

    tcp = Host("server", "bastion.example", edge=True, port=22, transport="tcp")
    assert topo.agent_url(tcp) == "tcp://bastion.example:22"
    internal = Host.internal("admin", "admin.policy.internal", 9000)
    assert topo.agent_url(internal) == "http://admin.policy.internal"


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("5000", 5000),
        ("5000:5000", 5000),
        ("127.0.0.1:80:5000", 5000),
        ("5000:5000/tcp", 5000),
        (8080, 8080),
        (PortMapping.model_validate({"target": 8080, "published": 80}), 8080),
        (PortMapping.model_validate({"target": "8080"}), 8080),  # numeric-string coercion
    ],
)
def test_container_port_parsing(entry: str | int | PortMapping, expected: int) -> None:
    assert container_port(entry) == expected


def test_port_mapping_rejects_non_numeric_target() -> None:
    with pytest.raises(ValidationError, match="Input should be a valid integer"):
        _ = PortMapping.model_validate({"target": "eighty"})


# --- gateway ---------------------------------------------------------------


def test_render_caddyfile_one_vhost_per_edge_host() -> None:
    topo = Topology(
        hosts=(
            Host(
                "app",
                "storefront.example",
                edge=True,
                port=5000,
                aliases=("store.example",),
            ),
            Host("admin", "admin.example", edge=False, port=9000),  # internal -> no vhost
            Host("ssh", "ssh.example", edge=True, port=22, transport="tcp"),
        ),
        domain="example",
    )
    caddy = render_caddyfile(topo)
    assert "http://storefront.example {" in caddy
    assert "reverse_proxy app:5000" in caddy
    assert "http://store.example {" in caddy
    assert "admin.example" not in caddy  # the expose-only service is never proxied
    assert "ssh.example" not in caddy  # raw TCP bypasses the HTTP gateway
    assert "/healthz" in caddy  # a health responder for the container healthcheck


# --- network synthesis -----------------------------------------------------


def test_attach_network_promotes_absent_and_null_attachments_for_aliases() -> None:
    absent = ComposeService()
    absent.attach_network("edge", ["app.example"])
    assert isinstance(absent.networks, dict)
    assert absent.networks["edge"] is not None
    assert absent.networks["edge"].aliases == ["app.example"]

    null_attachment = ComposeService.model_validate({"networks": {"backend": None}})
    null_attachment.attach_network("backend", ["vault.internal"])
    assert isinstance(null_attachment.networks, dict)
    assert null_attachment.networks["backend"] is not None
    assert null_attachment.networks["backend"].aliases == ["vault.internal"]


def test_attach_network_merges_aliases_without_losing_attachment_options() -> None:
    service = ComposeService.model_validate(
        {
            "networks": {
                "backend": {
                    "aliases": ["legacy.internal"],
                    "ipv4_address": "10.0.0.7",
                }
            }
        }
    )
    assert isinstance(service.networks, dict)
    original = service.networks["backend"]
    assert original is not None

    service.attach_network("backend", ["legacy.internal", "vault.internal", "vault.internal"])
    service.attach_network("backend", ["vault.internal"])

    assert service.networks["backend"] is original
    assert original.aliases == ["legacy.internal", "vault.internal"]
    assert original.model_dump(mode="python", exclude_unset=True) == {
        "aliases": ["legacy.internal", "vault.internal"],
        "ipv4_address": "10.0.0.7",
    }


def test_inject_network_adds_backend_edge_and_gateway(tmp_path: Path) -> None:
    compose = ComposeFile.model_validate(
        {"services": {"app": {"ports": ["5000"]}, "admin": {"expose": ["9000"]}}}
    )
    topo = build_topology(
        None, _services({"app": {"ports": ["5000:5000"]}, "admin": {"expose": ["9000"]}})
    )
    inject_network(compose, topo, tmp_path)

    assert compose.networks is not None
    assert set(compose.networks) == {"backend", "edge"}
    # Every task service is on the backend network only (never on the agent's edge).
    services = compose.services or {}
    assert services["app"].networks == ["backend"]
    assert services["admin"].networks == ["backend"]

    gateway = services["_gateway"]
    assert gateway.image is not None
    assert gateway.image.startswith("caddy")
    assert isinstance(gateway.networks, dict)
    edge = gateway.networks["edge"]
    assert edge is not None
    assert edge.aliases == ["app.svc.internal"]  # only the published host is proxied
    assert (tmp_path / "Caddyfile").is_file()


def test_inject_network_preserves_existing_service_networks(tmp_path: Path) -> None:
    compose = ComposeFile.model_validate(
        {
            "services": {
                "a": {"ports": ["1"], "networks": ["other"]},  # existing list -> append
                "b": {"expose": ["2"], "networks": {"other": {}}},  # existing dict -> add key
            },
        }
    )
    topo = build_topology(None, _services({"a": {"ports": ["1"]}, "b": {"expose": ["2"]}}))
    inject_network(compose, topo, tmp_path)
    services = compose.services or {}
    assert services["a"].networks == ["other", "backend"]
    b_networks = services["b"].networks
    assert isinstance(b_networks, dict)
    assert set(b_networks) == {"other", "backend"}


@pytest.mark.parametrize(
    ("existing", "expected_networks"),
    [
        (None, {"backend", "edge"}),
        (["other"], {"other", "backend", "edge"}),
        ({"other": {}}, {"other", "backend", "edge"}),
    ],
)
def test_inject_network_attaches_tcp_service_directly_to_edge(
    tmp_path: Path,
    existing: object,
    expected_networks: set[str],
) -> None:
    raw_service: dict[str, object] = {"ports": ["22"]}
    if existing is not None:
        raw_service["networks"] = existing
    compose = ComposeFile.model_validate({"services": {"server": raw_service}})
    topology = build_topology(
        NetworkSpec.model_validate(
            {
                "hosts": {
                    "server": {
                        "fqdn": "bastion.example",
                        "aliases": ["ssh.example"],
                        "transport": "tcp",
                    }
                }
            }
        ),
        _services({"server": raw_service}),
    )
    inject_network(compose, topology, tmp_path)

    networks = (compose.services or {})["server"].networks
    assert isinstance(networks, dict)
    assert set(networks) == expected_networks
    edge = networks["edge"]
    assert edge is not None
    assert edge.aliases == ["bastion.example", "ssh.example"]


def test_inject_network_ignores_direct_host_missing_from_compose(tmp_path: Path) -> None:
    compose = ComposeFile.model_validate({"services": {"app": {"ports": ["80"]}}})
    topology = Topology(
        hosts=(Host("missing", "ssh.example", edge=True, port=22, transport="tcp"),),
        domain="example",
    )
    inject_network(compose, topology, tmp_path)
    assert (compose.services or {})["app"].networks == ["backend"]


def test_inject_network_ignores_internal_alias_host_missing_from_compose(tmp_path: Path) -> None:
    compose = ComposeFile.model_validate({"services": {"app": {"ports": ["80"]}}})
    topology = Topology(
        hosts=(
            Host(
                "gone",
                "vault.corp.internal",
                edge=False,
                port=6000,
                aliases=("vault.corp.internal",),
            ),
        ),
        domain="corp.internal",
    )
    inject_network(compose, topology, tmp_path)
    assert (compose.services or {})["app"].networks == ["backend"]


def test_inject_network_is_idempotent_on_backend(tmp_path: Path) -> None:
    # A service already on `backend` is left as-is (no duplicate attach).
    compose = ComposeFile.model_validate(
        {"services": {"a": {"ports": ["1"], "networks": ["backend"]}}}
    )
    inject_network(compose, build_topology(None, _services({"a": {"ports": ["1"]}})), tmp_path)
    assert (compose.services or {})["a"].networks == ["backend"]


def test_inject_network_without_services_key(tmp_path: Path) -> None:
    compose = ComposeFile()  # no services mapping -> still gets the networks block
    inject_network(compose, build_topology(None, _services({"app": {"ports": ["1"]}})), tmp_path)
    assert compose.networks is not None
    assert set(compose.networks) == {"backend", "edge"}
    assert compose.services is None  # no gateway invented into a service-less compose


def test_compose_file_rejects_non_dict_service() -> None:
    # A non-mapping service is a docker-compose error too; it now fails at our parse instead
    # of being silently skipped.
    with pytest.raises(ValidationError, match="Input should be a valid dictionary"):
        _ = ComposeFile.model_validate({"services": {"x": "not-a-dict"}})


def test_compose_model_round_trip_is_lossless() -> None:
    original: dict[str, object] = {
        "version": "3.9",
        "x-custom": {"anything": [1, 2]},
        "services": {
            "app": {
                "build": {"context": "./c"},
                "environment": {"FOO": None, "BAR": "1"},
                "ports": ["5000:5000", 8080, {"target": 9090, "published": 90}],
                "depends_on": ["db"],
            },
            "admin": {"expose": [7000, "7001"], "networks": {"priv": None}},
            "db": {"networks": ["priv"]},
        },
        "networks": {"priv": {"driver": "bridge"}},
        "volumes": {"data": None},
    }
    dumped = ComposeFile.model_validate(original).model_dump(mode="python", exclude_unset=True)
    assert dumped == original


def test_network_names_follow_project() -> None:
    assert edge_network_name("sb-demo") == "sb-demo_edge"
    assert backend_network_name("sb-demo") == "sb-demo_backend"
