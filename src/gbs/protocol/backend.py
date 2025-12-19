"""Backend Protocol

Pure protocol definition for backends that participate in build planning.
This is used for type checking only - implementations should inherit from
gbs.base.BaseBackend instead.
"""

from __future__ import annotations
from typing import Protocol, Any, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from .pass_ import Pass
    from ..config.model import GBSConfig

__all__ = ["Backend"]


@runtime_checkable
class Backend(Protocol):
    """Protocol for backends that participate in build planning

    Backends contribute Pass instances for build planning. Each Pass
    declares input/output file types and can create Dispatchers for execution.

    The build planner queries backends to find transformation paths from
    source file types to desired output file types.

    Attributes:
        name: Unique backend identifier (e.g., "gbs.builtin.ghdl")
    """

    name: str

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: GBSConfig | None = None
    ) -> list[Pass]:
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
