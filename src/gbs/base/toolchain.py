"""Base ToolchainProvider Implementation

Abstract base class for toolchain providers. Subclass this to add
support for a new tool installer or vendor tree.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config.model import ToolConfig

__all__ = ["BaseToolchainProvider"]


class BaseToolchainProvider(ABC):
    """Base class for toolchain providers.

    Subclasses set the class attribute `type` (matching the `type:` key
    used in config) and implement `enumerate_tools()`. A provider
    instance is constructed once per `toolchains:` entry with the raw
    options dict from that entry.

    Attributes:
        type: Identifier used by config to select this provider.
    """

    type: str = None

    def __init__(self, options: dict[str, Any], origin: Optional[Path] = None):
        """Initialize provider.

        Args:
            options: Config dict for this toolchain entry (all keys except `type`).
            origin: Path of the config file this entry was declared in.
        """
        self.options = options
        self.origin = origin

    @abstractmethod
    def enumerate_tools(self) -> list["ToolConfig"]:
        """Return ToolConfig entries for tools this provider found."""
        ...
