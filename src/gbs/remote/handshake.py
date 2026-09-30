"""Connection handshake

The client opens a connection with a `hello` request carrying its
identity: protocol version, gbs version, plugin versions and digests
of the gbs and plugin sources, and whether source digests must match.
The server answers with its own identity and its tool inventory.

Both instances must run the same gbs, as the remote re-runs planning
and dispatch code for the client: any difference could build something
else than what the client planned. gbs sources must agree too, unless
the client opts out for the host; differences are then only warned
about. Source differences are detailed file by file, the client
fetching the remote file digests with `sources.files`.

Plugins may differ between the instances: each use of a plugin is
checked instead, see PluginCompatibility.

A source digest covers the own tree of a module only: the directory of
a regular package, even when its `__path__` is extended by other
installs, every `__path__` entry of a namespace package, or the file
of a plain module.
"""

from __future__ import annotations
import hashlib
import importlib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Optional

from .. import __version__ as gbs_version
from .toolhost import ToolDescription
from .wire import WireError, WireFormat, WireObject

__all__ = [
    "HandshakeError", "SourceDigest", "SourceFiles", "Identity", "PluginCompatibility",
    "Hello", "HelloReply",
]


class HandshakeError(Exception):
    """The other side cannot be worked with"""
    pass


class SourceDigest:
    """Digests of Python source trees

    Two checkouts carrying the same version number may hold different
    code. A file map gives the SHA-256 of every .py file below the
    tree roots, outside `__pycache__`, by relative path; the first
    root holding a path wins. A tree digest covers the file map, in
    path order. File maps are computed once per process.
    """

    __cache: dict[tuple[str, ...], dict[str, str]] = {}

    @staticmethod
    def roots(module: ModuleType) -> list[Path]:
        """Roots of the own sources of a module

        Raises:
            HandshakeError: If the module has no source file.
        """
        file = getattr(module, "__file__", None)
        paths = getattr(module, "__path__", None)
        if paths is not None:
            if file is not None:
                return [Path(file).parent]
            return [Path(p) for p in paths]
        if file is None:
            raise HandshakeError(f"Module {module.__name__} has no source file")
        return [Path(file)]

    @classmethod
    def module_files(cls, name: str) -> dict[str, str]:
        """File map of the own sources of a module"""
        return cls.files(cls.roots(importlib.import_module(name)))

    @classmethod
    def module(cls, name: str) -> str:
        """Digest of the own sources of a module"""
        return cls.digest(cls.module_files(name))

    @classmethod
    def tree(cls, roots: Iterable[Path]) -> str:
        """Digest of the .py files below roots, a root being a directory
        or a single file"""
        return cls.digest(cls.files(roots))

    @classmethod
    def files(cls, roots: Iterable[Path]) -> dict[str, str]:
        """File map of the .py files below roots, a root being a
        directory or a single file"""
        roots = [Path(r).resolve() for r in roots]
        key = tuple(str(r) for r in roots)
        files = cls.__cache.get(key)
        if files is None:
            files = cls.__compute(roots)
            cls.__cache[key] = files
        return dict(files)

    @staticmethod
    def digest(files: dict[str, str]) -> str:
        """Digest of a file map"""
        digest = hashlib.sha256()
        for relative in sorted(files):
            name = relative.encode("utf-8")
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(bytes.fromhex(files[relative]))
        return digest.hexdigest()

    @staticmethod
    def __compute(roots: list[Path]) -> dict[str, str]:
        paths: dict[str, Path] = {}
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
                paths.setdefault(relative, path)
        return {
            relative: hashlib.sha256(path.read_bytes()).hexdigest()
            for relative, path in sorted(paths.items())
        }


class SourceFiles:
    """Differences between the file maps of two source trees"""

    LIMIT = 10

    @staticmethod
    def from_json(data: Any, what: str) -> dict[str, str]:
        """Read a file map

        Raises:
            WireError: If the document is malformed.
        """
        if not isinstance(data, dict):
            raise WireError(f"{what}: expected an object, got {type(data).__name__}")
        for path, digest in data.items():
            if not isinstance(digest, str):
                raise WireError(f"{what}: digest of {path!r} must be a string")
        return dict(data)

    @classmethod
    def describe(cls, mine: dict[str, str], theirs: dict[str, str],
                 local: str, remote: str) -> list[str]:
        """Lines listing files that differ, up to LIMIT of them

        Args:
            mine: File map on this side
            theirs: File map on the other side
            local: Name of this side
            remote: Name of the other side
        """
        found = sorted(
            [(path, "differs") for path in mine.keys() & theirs.keys()
             if mine[path] != theirs[path]]
            + [(path, f"only on {local}") for path in mine.keys() - theirs.keys()]
            + [(path, f"only on {remote}") for path in theirs.keys() - mine.keys()]
        )
        lines = [f"{path}: {how}" for path, how in found[:cls.LIMIT]]
        if len(found) > cls.LIMIT:
            lines.append(f"and {len(found) - cls.LIMIT} more file(s)")
        return lines


@dataclass(frozen=True)
class Identity:
    """What identifies a gbs instance to another

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
    ABSENT = "not installed"

    @dataclass(frozen=True)
    class Difference:
        """A difference between two identities

        Attributes:
            what: Differing item, for messages
            mine: Value on this side, ABSENT for a plugin not installed
            theirs: Value on the other side
            source: Source key when this is a source digest difference
            plugin: Plugin name when this is a plugin difference, None
                for a gbs difference
        """
        what: str
        mine: str
        theirs: str
        source: Optional[str] = None
        plugin: Optional[str] = None

    @classmethod
    def source_modules(cls) -> dict[str, str]:
        """Module of each source key of this process

        Raises:
            HandshakeError: If a plugin was not registered from a
                module, so its sources are unknown.
        """
        from ..plugins import get_plugin_registry
        registry = get_plugin_registry()
        modules = {cls.GBS_SOURCES: "gbs"}
        for plugin in registry.get_all_plugins():
            module = registry.plugin_module(plugin.name)
            if module is None:
                raise HandshakeError(f"Plugin {plugin.name} was not registered from a module")
            modules[plugin.name] = module
        return modules

    @classmethod
    def local(cls) -> Identity:
        """Identity of this process

        Raises:
            HandshakeError: If a plugin was not registered from a
                module, so its sources are unknown.
        """
        from ..plugins import get_plugin_registry
        plugins = get_plugin_registry().get_all_plugins()
        return cls(
            protocol=WireFormat.VERSION,
            gbs=gbs_version,
            plugins={p.name: str(p.version) for p in plugins},
            sources={key: SourceDigest.module(module)
                     for key, module in cls.source_modules().items()},
        )

    @staticmethod
    def source_label(key: str) -> str:
        if key == Identity.GBS_SOURCES:
            return "gbs sources"
        return f"plugin {key} sources"

    def differences(self, other: Identity) -> list[Identity.Difference]:
        """Differences from another identity, gbs first, versions first"""
        found = []
        if self.protocol != other.protocol:
            found.append(self.Difference("protocol version", str(self.protocol), str(other.protocol)))
        if self.gbs != other.gbs:
            found.append(self.Difference("gbs version", self.gbs, other.gbs))
        mine = self.sources.get(self.GBS_SOURCES, self.ABSENT)
        theirs = other.sources.get(self.GBS_SOURCES, self.ABSENT)
        if mine != theirs:
            found.append(self.Difference(self.source_label(self.GBS_SOURCES), mine, theirs,
                                         self.GBS_SOURCES))
        for name in sorted(set(self.plugins) | set(other.plugins)):
            mine = self.plugins.get(name, self.ABSENT)
            theirs = other.plugins.get(name, self.ABSENT)
            if mine != theirs:
                found.append(self.Difference(f"plugin {name}", mine, theirs, plugin=name))
        for key in sorted((set(self.sources) | set(other.sources)) - {self.GBS_SOURCES}):
            mine = self.sources.get(key, self.ABSENT)
            theirs = other.sources.get(key, self.ABSENT)
            if mine != theirs:
                found.append(self.Difference(self.source_label(key), mine, theirs, key, key))
        return found

    def check(self, other: Identity, local: str, remote: str,
              check_sources: bool = True,
              files: Optional[dict[str, tuple[dict[str, str], dict[str, str]]]] = None) -> list[str]:
        """Refuse gbs differences with another identity

        Plugin differences are left to PluginCompatibility.

        Args:
            other: Identity of the other side
            local: Name of this side, for the message
            remote: Name of the other side, for the message
            check_sources: Whether gbs source differences are refused
            files: File maps on this side and on the other side, by
                source key, to detail source differences with

        Returns:
            Lines describing the tolerated differences, empty when
            there is none.

        Raises:
            HandshakeError: Listing every gbs difference, if one is
                refused.
        """
        files = files or {}
        lines = []
        refused = False
        for difference in self.differences(other):
            if difference.plugin is not None:
                continue
            lines.append(f"  {difference.what}: {difference.mine} on {local}, "
                         f"{difference.theirs} on {remote}")
            if difference.source is None or check_sources:
                refused = True
            if difference.source in files:
                mine, theirs = files[difference.source]
                lines.extend(f"    {line}" for line in SourceFiles.describe(mine, theirs, local, remote))
        if refused:
            raise HandshakeError(f"{remote} runs a gbs incompatible with {local}:\n" + "\n".join(lines))
        return lines

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


class PluginCompatibility:
    """Which plugins may be used with a connected gbs instance

    A plugin is compatible when it is installed on both sides with the
    same version and, when sources are checked, the same source digest.
    A plugin that is not is left out of what the other side does for
    this one: its backends contribute no pass there, and its generic
    dispatchers are skipped in segments dispatched there.

    Attributes:
        mine: Identity of this side
        theirs: Identity of the other side
        remote: Name of the other side, for messages
        check_sources: Whether plugin source differences make plugins
            incompatible
    """

    def __init__(self, mine: Identity, theirs: Identity, remote: str, check_sources: bool):
        self.mine = mine
        self.theirs = theirs
        self.remote = remote
        self.check_sources = check_sources

    def problem(self, name: str) -> Optional[str]:
        """None when a plugin is compatible, the reason otherwise"""
        if name not in self.theirs.plugins:
            return f"plugin {name} is not installed on {self.remote}"
        if name not in self.mine.plugins:
            return f"plugin {name} is not installed here"
        if self.mine.plugins[name] != self.theirs.plugins[name]:
            return (f"plugin {name} version {self.mine.plugins[name]} here, "
                    f"{self.theirs.plugins[name]} on {self.remote}")
        if self.check_sources and self.sources_differ(name):
            return f"plugin {name} sources differ on {self.remote}"
        return None

    def sources_differ(self, name: str) -> bool:
        return self.mine.sources.get(name) != self.theirs.sources.get(name)

    def status(self, name: str, local: str) -> str:
        """How a plugin on the other side relates to this side

        Args:
            name: Plugin name
            local: Name of this side, for the message
        """
        if name not in self.mine.plugins:
            return f"not installed on {local}"
        if name not in self.theirs.plugins:
            return f"not installed on {self.remote}"
        if self.mine.plugins[name] != self.theirs.plugins[name]:
            return f"version differs, {self.mine.plugins[name]} on {local}"
        if self.sources_differ(name):
            return "sources differ" if self.check_sources else "sources differ, not checked"
        return "same"

    def problems(self, names: Iterable[str]) -> dict[str, str]:
        """Reason of each incompatible plugin among names, by name"""
        found = {}
        for name in sorted(set(names)):
            problem = self.problem(name)
            if problem is not None:
                found[name] = problem
        return found


@dataclass(frozen=True)
class Hello:
    """What the client sends with `hello`

    Attributes:
        identity: Client identity
        check_sources: Whether source digest differences are refused
    """
    identity: Identity
    check_sources: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_json(),
            "check_sources": self.check_sources,
        }

    @classmethod
    def from_json(cls, data: Any) -> Hello:
        """Read hello parameters, the identity first

        Raises:
            HandshakeError: If the protocol version differs.
            WireError: If the document is malformed.
        """
        reader = WireObject(data, "hello params")
        identity = Identity.from_json(reader.field("identity", dict))
        check_sources = reader.field("check_sources", bool)
        reader.finish()
        return cls(identity, check_sources)


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
