"""Backend System for GBS

This module defines the Backend interface for build planning.

Backends contribute Pass objects for build planning. Each Pass can then
create Dispatchers for execution. This creates a hierarchical structure:

    Backend -> Pass -> Dispatcher -> Task

Key concepts:
- Backend: Top-level plugin that contributes passes based on desired outputs
- Pass: Planning metadata describing file type transformations, creates dispatchers
- Dispatcher: Execution engine that processes BuildFileSet and creates tasks
"""

from __future__ import annotations
from typing import Protocol, Any, runtime_checkable
from abc import ABC, abstractmethod

from ..logging import get_logger

__all__ = ["Backend", "BaseBackend"]

@runtime_checkable
class Backend(Protocol):
    """Protocol for backends that participate in build planning

    Backends contribute Pass instances for build planning. Each Pass
    declares input/output file types and can create Dispatchers for execution.

    The build planner queries backends to find transformation paths from
    source file types to desired output file types.
    """

    name: str

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list['Pass']:
        """Contribute Pass instances for build planning

        Backends examine the requested output types and configuration,
        then return Pass instances they can provide.

        Args:
            config: Backend-specific configuration from OutputGroup.backend_config
            output_types: Set of desired output file types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances this backend can contribute

        Example:
            def contribute_passes(self, config, output_types, project_config=None, gbs_config=None):
                passes = []
                if "ghdl-simulator" in output_types:
                    passes.append(GHDLSimulatePass(config, project_config, gbs_config))
                return passes
        """
        ...

class BaseBackend(ABC):
    """Base class for backends

    Provides common functionality and enforces the Backend protocol.
    Subclasses must implement contribute_passes().
    """

    def __init__(self, name: str):
        """Initialize backend

        Args:
            name: Unique backend name (e.g., "gbs.backend.ghdl")
        """
        self.name = name
        self.logger = get_logger(f"Backend({name})")

    @abstractmethod
    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list['Pass']:
        """Contribute Pass classes for build planning

        Must be implemented by subclasses.
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name})"


# Import Pass and Dispatcher here to avoid circular imports
# These are used in type hints only
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .passes import Pass
    from .dispatcher import Dispatcher
