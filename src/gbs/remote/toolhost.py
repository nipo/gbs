"""Hosts tools run on

A ToolHost answers which tools a host provides and whether each is
usable there. The local host reads the local configuration; a remote
host answers from the inventory its gbs instance sent at connection.
Only identifiers and a usability verdict cross the wire; tool paths
and environment stay a concern of the host that runs the tool.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from ..config.model import GBSConfig, ToolConfig
from ..utils import expand_path
from .wire import WireObject

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

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name!r})"


class LocalToolHost(ToolHost):
    """The host gbs runs on, as its configuration describes it"""

    def __init__(self, gbs_config: GBSConfig, name: str = "local host"):
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
        return [
            ToolDescription(
                t.name,
                None if t.variant is None else str(t.variant),
                None if t.version is None else str(t.version),
                self.tool_problem(t),
            )
            for t in self.gbs_config.tools
        ]


class RemoteToolHost(ToolHost):
    """A remote host, as described by its inventory"""

    def __init__(self, name: str, tools: Iterable[ToolDescription]):
        super().__init__(name)
        self.__tools = list(tools)

    def tools(self) -> list[ToolDescription]:
        return list(self.__tools)
