"""ToolchainProvider Protocol

Discovers multiple tools from a shared install prefix.
Used for type checking only; implementations should inherit from
gbs.base.BaseToolchainProvider.
"""

from __future__ import annotations
from typing import Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config.model import ToolConfig

__all__ = ["ToolchainProvider"]


@runtime_checkable
class ToolchainProvider(Protocol):
    """Turns a `toolchains:` config entry into a list of ToolConfig entries.

    Providers are keyed by their `type` attribute, matched against the
    `type:` field in a config `toolchains:` entry. A provider instance is
    constructed with the entry's options dict (all keys except `type`)
    and the path of the config file the entry came from.

    Providers must only return tools they actually discovered. Missing
    packages or executables are silently skipped so partial installs
    still yield partial toolchains; the explicit `tools:` section is
    the escape hatch to fill in what a provider cannot detect.

    Attributes:
        type: The `type:` identifier this provider handles.
    """

    type: str

    def enumerate_tools(self) -> list[ToolConfig]:
        """Return ToolConfig entries for tools this provider found."""
        ...
