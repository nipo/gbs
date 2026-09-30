"""Hosts tools run on

A ToolHost answers which tools a host provides and whether each is
usable there, and which passes its backends contribute for a planning
query. The local host reads the local configuration; a remote host
answers from the inventory its gbs instance sent at connection, and
forwards planning queries to it. Only identifiers, usability verdicts
and planning interfaces cross the wire; tool paths and environment
stay a concern of the host that runs the tool.
"""

from __future__ import annotations
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Optional

from ..config.model import GBSConfig, ToolConfig
from ..utils import expand_path
from .wire import WireError, WireFormat, WireObject

if TYPE_CHECKING:
    from ..protocol import Backend
    from .peer import Peer

__all__ = ["ToolDescription", "ToolHost", "LocalToolHost", "RemoteToolHost"]


@dataclass(frozen=True)
class ToolDescription:
    """A tool as a host advertises it

    Attributes:
        name: Tool name
        variant: User-declared variant, None if unset
        version: Declared version, None if unset
        problem: Why the tool is not usable on its host, None if usable
    """
    name: str
    variant: Optional[str] = None
    version: Optional[str] = None
    problem: Optional[str] = None

    @property
    def identifier(self) -> str:
        return ToolConfig(self.name, self.variant, self.version).identifier

    def matches(self, identifier: str) -> bool:
        """Whether the tool fits a 'name[:variant][@version]' identifier"""
        name, variant, version = GBSConfig._parse_identifier(identifier)
        return (self.name == name
                and (variant is None or self.variant == variant)
                and (version is None or self.version == version))

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "variant": self.variant,
            "version": self.version,
            "problem": self.problem,
        }

    @classmethod
    def from_json(cls, data: Any) -> ToolDescription:
        reader = WireObject(data, "tool description")
        tool = cls(
            name=reader.field("name", str),
            variant=reader.field("variant", str, type(None)),
            version=reader.field("version", str, type(None)),
            problem=reader.field("problem", str, type(None)),
        )
        reader.finish()
        return tool


class ToolHost(ABC):
    """A host tools may run on

    Attributes:
        name: Host name for diagnostics
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def tools(self) -> list[ToolDescription]:
        """Every tool the host declares, usable or not, in lookup order"""
        ...

    def tool_find(self, identifier: str) -> Optional[ToolDescription]:
        """First declared tool matching 'name[:variant][@version]'"""
        for tool in self.tools():
            if tool.matches(identifier):
                return tool
        return None

    def tool_probe(self, identifier: str) -> Optional[str]:
        """None when the host can run the tool, a reason otherwise"""
        tool = self.tool_find(identifier)
        if tool is None:
            return f"tool {identifier!r} not configured on {self.name}"
        return tool.problem

    @abstractmethod
    async def passes_contribute(self,
                                backend: Backend,
                                config: dict[str, Any],
                                requested_types: set[str],
                                project_config: dict[str, Any]) -> list[tuple[Any, Optional[str]]]:
        """Passes a backend contributes on this host, with their probe verdict

        Args:
            backend: Backend to query
            config: Backend configuration, target and tool overrides
                included
            requested_types: Output types the backend is asked for
            project_config: Project configuration

        Returns:
            (pass, problem) for each contributed pass, problem being
            None when the pass is usable on this host
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name!r})"


class LocalToolHost(ToolHost):
    """The host gbs runs on, as its configuration describes it

    Without a configuration, no tool is known.
    """

    def __init__(self, gbs_config: Optional[GBSConfig], name: str = "local host"):
        super().__init__(name)
        self.gbs_config = gbs_config

    @staticmethod
    def tool_problem(tool: ToolConfig) -> Optional[str]:
        """Why a configured tool is not usable, None if it is

        A tool declaring an `executable` or `path` is usable when it
        exists. A tool declaring neither relies on $PATH and is
        trusted.
        """
        for key in ("executable", "path"):
            raw = tool.config.get(key)
            if not raw:
                continue
            resolved = expand_path(raw)
            if not resolved.exists():
                return f"tool {tool.identifier!r} {key} {resolved} does not exist"
            return None
        return None

    def tools(self) -> list[ToolDescription]:
        if self.gbs_config is None:
            return []
        return [
            ToolDescription(
                t.name,
                None if t.variant is None else str(t.variant),
                None if t.version is None else str(t.version),
                self.tool_problem(t),
            )
            for t in self.gbs_config.tools
        ]

    def tool_probe(self, identifier: str) -> Optional[str]:
        """None when the tool is usable here, a reason otherwise

        The tool is looked up the way passes resolve it at run time.
        """
        if self.gbs_config is None:
            return f"no GBS configuration on {self.name}; tool {identifier!r} unresolvable"
        tool = self.gbs_config.get_tool(identifier)
        if tool is None:
            return f"tool {identifier!r} not configured on {self.name}"
        return self.tool_problem(tool)

    async def passes_contribute(self, backend, config, requested_types, project_config):
        passes = backend.contribute_passes(config, requested_types, project_config, self.gbs_config)
        return [(pass_obj, pass_obj.probe()) for pass_obj in passes]


class RemoteToolHost(ToolHost):
    """A remote host, as described by its inventory

    Planning queries are forwarded to the remote gbs instance through
    the `passes.contribute` request. Answers are kept for the lifetime
    of the connection, as planning repeats the same queries.

    Attributes:
        peer: Connection to the remote, None for an inventory alone
    """

    def __init__(self, name: str, tools: Iterable[ToolDescription], peer: Optional[Peer]):
        super().__init__(name)
        self.peer = peer
        self.__tools = list(tools)
        self.__contributions: dict[str, list] = {}

    def tools(self) -> list[ToolDescription]:
        return list(self.__tools)

    async def passes_contribute(self, backend, config, requested_types, project_config):
        from .planning import PassContribution, RemotePass

        if self.peer is None:
            raise RuntimeError(f"{self.name}: no connection to query passes from")
        params = {
            "backend": backend.name,
            "config": WireFormat.json_check(config, f"configuration of backend {backend.name}"),
            "requested_types": sorted(requested_types),
            "project_config": WireFormat.json_check(project_config, "project configuration"),
        }
        key = json.dumps(params, sort_keys=True)
        contributions = self.__contributions.get(key)
        if contributions is None:
            reply = await self.peer.request("passes.contribute", params)
            contributions = PassContribution.list_from_json(reply.result)
            for contribution in contributions:
                if contribution.descriptor.backend != backend.name:
                    raise WireError(
                        f"{self.name}: backend {backend.name} query answered with "
                        f"pass {contribution.descriptor.name} of backend "
                        f"{contribution.descriptor.backend}"
                    )
            self.__contributions[key] = contributions
        return [(RemotePass(self.name, c), c.problem) for c in contributions]
