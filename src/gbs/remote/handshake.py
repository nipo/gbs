"""Connection handshake

The client opens a connection with a `hello` request carrying its
identity: protocol version, gbs version and plugin versions. The server
answers with its own identity and its tool inventory. Both instances
must agree on the identity exactly, as the remote re-runs planning and
dispatch code for the client: any difference could build something
else than what the client planned.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from .. import __version__ as gbs_version
from .toolhost import ToolDescription
from .wire import WireError, WireFormat, WireObject

__all__ = ["HandshakeError", "Identity", "HelloReply"]


class HandshakeError(Exception):
    """The other side cannot be worked with"""
    pass


@dataclass(frozen=True)
class Identity:
    """What must match between two connected gbs instances

    Attributes:
        protocol: Wire format version
        gbs: gbs version
        plugins: Version of each loaded plugin, by plugin name
    """
    protocol: int
    gbs: str
    plugins: dict[str, str]

    @classmethod
    def local(cls) -> Identity:
        from ..plugins import get_plugin_registry
        return cls(
            protocol=WireFormat.VERSION,
            gbs=gbs_version,
            plugins={p.name: str(p.version) for p in get_plugin_registry().get_all_plugins()},
        )

    def mismatches(self, other: Identity) -> list[tuple[str, str, str]]:
        """Differences from another identity

        Returns:
            (what, value here, value in other) for each difference,
            "missing" standing for an absent plugin.
        """
        found = []
        if self.protocol != other.protocol:
            found.append(("protocol version", str(self.protocol), str(other.protocol)))
        if self.gbs != other.gbs:
            found.append(("gbs version", self.gbs, other.gbs))
        for name in sorted(set(self.plugins) | set(other.plugins)):
            mine = self.plugins.get(name, "missing")
            theirs = other.plugins.get(name, "missing")
            if mine != theirs:
                found.append((f"plugin {name}", mine, theirs))
        return found

    def check(self, other: Identity, local: str, remote: str) -> None:
        """Refuse any difference with another identity

        Args:
            other: Identity of the other side
            local: Name of this side, for the message
            remote: Name of the other side, for the message

        Raises:
            HandshakeError: Listing every difference.
        """
        found = self.mismatches(other)
        if found:
            raise HandshakeError(
                f"{remote} runs a gbs incompatible with {local}:\n"
                + "\n".join(f"  {what}: {mine} on {local}, {theirs} on {remote}"
                            for what, mine, theirs in found)
            )

    def to_json(self) -> dict[str, Any]:
        return {"protocol": self.protocol, "gbs": self.gbs, "plugins": dict(self.plugins)}

    @classmethod
    def from_json(cls, data: Any) -> Identity:
        """Read an identity

        The protocol version is checked before anything else, as the
        rest of the layout depends on it.

        Raises:
            HandshakeError: If the protocol version differs.
            WireError: If the document is malformed.
        """
        reader = WireObject(data, "identity")
        protocol = reader.field("protocol", int)
        if protocol != WireFormat.VERSION:
            raise HandshakeError(
                f"Protocol version {protocol} is not supported (expected {WireFormat.VERSION})"
            )
        gbs = reader.field("gbs", str)
        plugins = reader.field("plugins", dict)
        reader.finish()
        for name, version in plugins.items():
            if not isinstance(version, str):
                raise WireError(f"identity: plugin {name!r} version must be a string")
        return cls(protocol, gbs, dict(plugins))


@dataclass(frozen=True)
class HelloReply:
    """What the server answers to `hello`

    Attributes:
        identity: Server identity
        tools: Server tool inventory
    """
    identity: Identity
    tools: list[ToolDescription]

    def to_json(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_json(),
            "tools": [t.to_json() for t in self.tools],
        }

    @classmethod
    def from_json(cls, data: Any) -> HelloReply:
        reader = WireObject(data, "hello reply")
        identity = Identity.from_json(reader.field("identity", dict))
        tools = [ToolDescription.from_json(t) for t in reader.field("tools", list)]
        reader.finish()
        return cls(identity, tools)
