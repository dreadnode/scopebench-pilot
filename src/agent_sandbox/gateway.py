"""Generate the Caddyfile for a task's synthesized edge gateway.

The gateway is a single ``caddy`` container, dual-homed on the ``edge`` (agent-facing) and
``backend`` networks. For every published HTTP service it serves the configured FQDNs on port 80
and reverse-proxies to the service's real ``name:port`` on the backend. The agent therefore reaches
HTTP services as ``http://storefront.vesta-market.example`` — no ephemeral host port and a normal
``Server:`` banner. Raw-TCP services bypass this gateway; ``expose``-only services stay
unreachable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_sandbox.topology import Topology

# A catch-all site so the container healthcheck (``Host: localhost``) gets a 200 without needing a
# real vhost; genuine traffic carries a published FQDN and matches its specific block instead.
_HEALTH_BLOCK = ":80 {\n\trespond /healthz 200\n}"


def render_caddyfile(topology: Topology) -> str:
    """Render the Caddyfile: a health responder plus one reverse-proxy vhost per published host."""
    blocks = [_HEALTH_BLOCK]
    blocks.extend(
        f"http://{name} {{\n\treverse_proxy {binding.host.service}:{binding.host.port}\n}}"
        for binding in topology.routes.gateway
        for name in binding.route.names
    )
    return "\n\n".join(blocks) + "\n"
