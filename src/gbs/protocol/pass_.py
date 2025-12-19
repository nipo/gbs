"""Pass Protocol

Pure protocol definition for passes that describe file type transformations.
This is used for type checking only - implementations should inherit from
gbs.base.BasePass instead.
"""

from __future__ import annotations
from typing import Protocol, runtime_checkable, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .dispatcher import Dispatcher
    from ..build.context import BuildContext
    from ..config.model import GBSConfig

__all__ = ["Pass"]


@runtime_checkable
class Pass(Protocol):
    """Protocol for passes that describe file type transformations

    A Pass is PURE PLANNING METADATA. It does NOT execute build tools.
    Execution happens via Dispatchers after planning is complete.

    A Pass declares:
    - name: Human-readable identifier
    - input_types: Set of required input file types
    - output_types: Set of produced output file types
    - filter_vars(): Optional filter variables for source selection
    - dispatchers(): Factory method to create dispatcher instances

    The build planner queries backends for Passes, then uses them to find
    transformation chains from available sources to desired outputs.

    Attributes:
        name: Pass name (e.g., "ghdl-simulate", "gowin-synthesize")
        input_types: Set of input file types this pass requires
        output_types: Set of output file types this pass produces
        can_fork: If True, planner may explore multiple paths through this pass
        priority: Planning priority (lower = preferred, default 100)
        types_with_library: File types requiring library classification
        config: Backend-specific configuration
        project_config: Project-level configuration
        gbs_config: GBS configuration
    """

    name: str
    input_types: set[str]
    output_types: set[str]
    can_fork: bool
    priority: int
    types_with_library: set[str]
    config: dict[str, Any]
    project_config: dict[str, Any]
    gbs_config: GBSConfig | None

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for source enumeration

        Passes can provide filter variables that will be merged with the
        OutputGroup's filter_vars before enumerating sources. This allows
        passes to request specific sources based on their needs.

        The planner combines filter_vars from ALL passes in a selected plan
        before enumerating sources. This ensures the source set matches
        what all passes expect.

        Returns:
            Dictionary of filter variable name -> value
        """
        ...

    def dispatchers(self, context: BuildContext) -> list[Dispatcher]:
        """Create dispatchers for executing this pass transformations

        Args:
            context: Build context to pass to dispatcher constructors

        Returns:
            Dispatcher instance list
        """
        ...
