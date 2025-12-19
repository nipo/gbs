"""Base Backend Implementation

Abstract base class for backends. Subclass this to create new backends.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

from ..logging import get_logger

if TYPE_CHECKING:
    from ..protocol import Pass
    from ..config.model import GBSConfig

__all__ = ["BaseBackend"]


class BaseBackend(ABC):
    """Base class for backends

    Provides common functionality and enforces the Backend protocol.
    Subclasses must implement contribute_passes().

    Attributes:
        name: Unique backend name (e.g., "gbs.builtin.ghdl")
        logger: Logger instance for this backend
    """

    def __init__(self, name: str):
        """Initialize backend

        Args:
            name: Unique backend name (e.g., "gbs.builtin.ghdl")
        """
        self.name = name
        self.logger = get_logger(f"Backend({name})")

    @abstractmethod
    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: GBSConfig | None = None
    ) -> list[Pass]:
        """Contribute Pass instances for build planning

        Must be implemented by subclasses.

        Args:
            config: Backend-specific configuration from OutputGroup.backend_config
            output_types: Set of desired output file types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances this backend can contribute
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name})"
