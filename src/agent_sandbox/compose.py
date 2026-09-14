"""Typed models for the slice of a Docker Compose file the sandbox reads and writes.

Only the keys the sandbox and benchmark touch are modelled: per-service ``networks``/``ports``/
``expose`` (read), the keys the synthesized gateway writes (``image``/``volumes``/``healthcheck``),
and the top-level ``networks`` block. Every other key is preserved verbatim via ``extra="allow"``
and round-trips through ``model_dump(mode="python", exclude_unset=True)`` — the dump contains
exactly the original keys plus whatever a writer explicitly set. Lifecycle management lives in
:mod:`agent_sandbox.stack`, keeping these models free of topology/runtime import cycles.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class _ComposeModel(BaseModel):
    """Base for the compose models: keep unknown compose keys so re-serialization is lossless."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")


class PortMapping(_ComposeModel):
    """A long-form ``ports`` entry (``{target: ..., published: ...}``); only ``target`` is read."""

    target: int = Field(description="The container port the service listens on.")


class ServiceNetworkAttachment(_ComposeModel):
    """Per-network attachment options in a service's dict-form ``networks`` block."""

    aliases: list[str] | None = Field(default=None, description="Extra DNS names on this network.")


class Healthcheck(_ComposeModel):
    """A compose ``healthcheck`` block (the fields the synthesized gateway writes)."""

    test: str | list[str] | None = Field(
        default=None, description="Probe command (CMD list or shell string)."
    )
    interval: str | None = Field(default=None, description="Time between probes.")
    timeout: str | None = Field(default=None, description="Per-probe timeout.")
    retries: int | None = Field(default=None, description="Consecutive failures before unhealthy.")
    start_period: str | None = Field(
        default=None, description="Grace period before failures count."
    )


class ComposeService(_ComposeModel):
    """One ``services.<name>`` mapping: the keys this project reads or writes."""

    image: str | None = Field(default=None, description="Container image reference.")
    networks: list[str] | dict[str, ServiceNetworkAttachment | None] | None = Field(
        default=None,
        description="Networks the service joins: short list form, or dict form with per-network "
        + "options (whose values may legitimately be null in YAML).",
    )
    ports: list[str | int | PortMapping] | None = Field(
        default=None,
        description="Published ports: short strings, bare ints, or long-form mappings. "
        + "Presence marks the service agent-facing (edge).",
    )
    expose: list[str | int] | None = Field(
        default=None,
        description="Container-only ports (reachable on the backend network, never published).",
    )
    volumes: list[str | dict[str, object]] | None = Field(
        default=None,
        description="Volume mounts; short strings are written by this project, long-form "
        + "mappings pass through unread.",
    )
    healthcheck: Healthcheck | None = Field(default=None, description="Container health probe.")

    def attach_network(self, name: str, aliases: Iterable[str] = ()) -> None:
        """Attach this service to ``name`` without losing existing Compose options.

        Compose accepts both a short list form and a long mapping form. Ordinary attachments keep
        the existing representation; aliases require the long form, so short entries are promoted
        to empty :class:`ServiceNetworkAttachment` objects before the aliases are merged.
        """
        requested_aliases = tuple(dict.fromkeys(aliases))
        existing = self.networks
        if not requested_aliases:
            if existing is None:
                self.networks = [name]
            elif isinstance(existing, list):
                if name not in existing:
                    existing.append(name)
            else:
                _ = existing.setdefault(name, ServiceNetworkAttachment())
            return

        if existing is None:
            networks: dict[str, ServiceNetworkAttachment | None] = {}
            self.networks = networks
        elif isinstance(existing, list):
            networks = {network: ServiceNetworkAttachment() for network in existing}
            self.networks = networks
        else:
            networks = existing

        attachment = networks.get(name)
        if attachment is None:
            attachment = ServiceNetworkAttachment()
            networks[name] = attachment
        attachment.aliases = list(dict.fromkeys([*(attachment.aliases or []), *requested_aliases]))


class ComposeFile(_ComposeModel):
    """A parsed compose file root; unmodelled top-level keys pass through untouched.

    Both fields default to ``None`` rather than empty containers: under the
    ``model_dump(mode="python", exclude_unset=True)`` round-trip, mutating a defaulted-but-unset
    collection in place would silently vanish from the dump, so absent blocks stay ``None`` and
    writers assign whole values instead.
    """

    services: dict[str, ComposeService] | None = Field(
        default=None,
        description="Service name to definition; every value must be a mapping "
        + "(as docker compose itself requires).",
    )
    networks: dict[str, object] | None = Field(
        default=None, description="Top-level network definitions; values pass through unread."
    )


def container_port(entry: str | int | PortMapping) -> int:
    """Extract the container port from a compose ``ports``/``expose`` entry.

    Handles short strings (``"5000"``, ``"5000:5000"``, ``"127.0.0.1:80:5000"``,
    ``"5000:5000/tcp"``), bare ints, and long-form mappings (``{target: 5000, ...}``).
    """
    match entry:
        case PortMapping(target=target):
            return target
        case _:
            token = str(entry).split("/", 1)[0]  # drop /tcp or /udp
            return int(token.split(":")[-1])
