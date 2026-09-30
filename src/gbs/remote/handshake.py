"""Connection handshake

The client opens a connection with a `hello` request carrying its
identity: protocol version, gbs version, plugin versions and digests
of the gbs and plugin sources. The server
answers with its own identity and its tool inventory. Both instances
must agree on the identity exactly, as the remote re-runs planning and
dispatch code for the client: any difference could build something
else than what the client planned.
"""

from __future__ import annotations
import hashlib
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .. import __version__ as gbs_version
from .toolhost import ToolDescription
from .wire import WireError, WireFormat, WireObject

__all__ = ["HandshakeError", "SourceDigest", "Identity", "HelloReply"]


class HandshakeError(Exception):
    """The other side cannot be worked with"""
    pass


class SourceDigest:
    """Digests of Python source trees

    Two checkouts carrying the same version number may hold different
    code. A tree digest covers the relative path and content of every
    .py file below the tree roots, in path order. Digests are computed
    once per process.
    """

    __cache: dict[tuple[str, ...], str] = {}

    @classmethod
    def module(cls, name: str) -> str:
        """Digest of the sources of a module, its whole tree for a package"""
        module = importlib.import_module(name)
        paths = getattr(module, "__path__", None)
        if paths is not None:
            return cls.tree([Path(p) for p in paths])
        if module.__file__ is None:
            raise HandshakeError(f"Module {name} has no source file")
        return cls.tree([Path(module.__file__)])

    @classmethod
    def tree(cls, roots: Iterable[Path]) -> str:
        """Digest of the .py files below roots, a root being a directory
        or a single file"""
        roots = [Path(r).resolve() for r in roots]
        key = tuple(str(r) for r in roots)
        digest = cls.__cache.get(key)
        if digest is None:
            digest = cls.__compute(roots)
            cls.__cache[key] = digest
        return digest

    @staticmethod
    def __compute(roots: list[Path]) -> str:
        files: dict[str, Path] = {}
        for root in roots:
            if root.is_file():
                candidates = [(root.name, root)]
            else:
                candidates = [
                    (path.relative_to(root).as_posix(), path)
                    for path in root.rglob("*.py")
                    if "__pycache__" not in path.relative_to(root).parts
                ]
            for relative, path in candidates:
                files.setdefault(relative, path)
        digest = hashlib.sha256()
        for relative in sorted(files):
            content = files[relative].read_bytes()
            name = relative.encode("utf-8")
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(len(content).to_bytes(8, "big") + content)
        return digest.hexdigest()


@dataclass(frozen=True)
class Identity:
    """What must match between two connected gbs instances

    Attributes:
        protocol: Wire format version
        gbs: gbs version
        plugins: Version of each loaded plugin, by plugin name
        sources: Source digest of gbs, under key "gbs", and of each
            loaded plugin, by plugin name
    """
    protocol: int
    gbs: str
    plugins: dict[str, str]
    sources: dict[str, str]

    GBS_SOURCES = "gbs"

    @classmethod
    def local(cls) -> Identity:
        """Identity of this process

        Raises:
            HandshakeError: If a plugin was not registered from a
                module, so its sources are unknown.
        """
        from ..plugins import get_plugin_registry
        registry = get_plugin_registry()
        plugins = registry.get_all_plugins()
        sources = {cls.GBS_SOURCES: SourceDigest.module("gbs")}
        for plugin in plugins:
            module = registry.plugin_module(plugin.name)
            if module is None:
                raise HandshakeError(f"Plugin {plugin.name} was not registered from a module")
            sources[plugin.name] = SourceDigest.module(module)
        return cls(
            protocol=WireFormat.VERSION,
            gbs=gbs_version,
            plugins={p.name: str(p.version) for p in plugins},
            sources=sources,
        )

    @staticmethod
    def source_label(key: str) -> str:
        if key == Identity.GBS_SOURCES:
            return "gbs sources"
        return f"plugin {key} sources"

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
        for key in sorted(set(self.sources) | set(other.sources)):
            mine = self.sources.get(key, "missing")
            theirs = other.sources.get(key, "missing")
            if mine != theirs:
                found.append((self.source_label(key), mine, theirs))
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
        return {
            "protocol": self.protocol,
            "gbs": self.gbs,
            "plugins": dict(self.plugins),
            "sources": dict(self.sources),
        }

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
        sources = reader.field("sources", dict)
        reader.finish()
        for name, version in plugins.items():
            if not isinstance(version, str):
                raise WireError(f"identity: plugin {name!r} version must be a string")
        for key, digest in sources.items():
            if not isinstance(digest, str):
                raise WireError(f"identity: {cls.source_label(key)} digest must be a string")
        return cls(protocol, gbs, dict(plugins), dict(sources))


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
