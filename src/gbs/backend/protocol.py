"""Backend System for GBS

This module defines the Backend interface for build planning and dispatcher creation.

Backends have two roles:
1. Planning: Contribute Pass objects that declare file type transformations
2. Execution: Create Dispatcher instances that build the actual task graph

Key concepts:
- Backend: Provides planning metadata and creates dispatchers
- Pass: Planning metadata describing file type transformations (input/output types)
- Dispatcher: Creates BuildSteps/Tasks graph from sources (execution)
"""

from __future__ import annotations
from typing import Protocol, Any, runtime_checkable
from abc import ABC, abstractmethod

from ..logging import get_logger

__all__ = ["Backend", "BaseBackend"]

@runtime_checkable
class Backend(Protocol):
    """Protocol for backends that participate in build planning

    Backends provide two key capabilities:
    1. contribute_passes(): Return Pass objects for build planning
    2. create_dispatcher(): Create a Dispatcher for execution

    The build planner queries backends to find transformation paths from
    source file types to desired output file types. Once a plan is selected,
    the backend creates a dispatcher to execute the plan.
    """

    name: str

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str]
    ) -> list[type['Pass']]:
        """Contribute Pass classes for build planning

        Backends examine the requested output types and configuration,
        then return Pass classes they can provide. Passes may fork
        (provide multiple alternatives) or chain (require intermediate types).

        Args:
            config: Backend-specific configuration from OutputGroup.backend_config
            output_types: Set of desired output file types

        Returns:
            List of Pass classes this backend can contribute

        Example:
            # GHDL backend for simulation
            if "ghdl-simulator" in output_types:
                return [GhdlSimulatePass]

            # Gowin backend for synthesis (provides multiple passes)
            if any(t in output_types for t in ["gowin-fs", "gowin-bin"]):
                return [GowinSynthesisPass, GowinBitstreamPass]
        """
        ...

class BaseBackend(ABC):
    """Base class for backends

    Provides common functionality and enforces the Backend protocol.
    Subclasses must implement contribute_passes() and create_dispatcher().
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
        output_types: set[str]
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
