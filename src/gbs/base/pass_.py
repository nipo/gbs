"""Base Pass Implementation

Concrete base class for passes. Subclass this to create new passes.
"""

from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocol import Dispatcher
    from ..build.context import BuildContext
    from ..config.model import GBSConfig

__all__ = ["BasePass", "resolve_tool_identifier"]


def resolve_tool_identifier(config: dict[str, Any], default_name: str) -> str:
    """Combine backend `tool` and `tool_version` into a single identifier.

    `config["tool"]` carries 'name[:variant][@version]'; `config["tool_version"]`
    is a separate scalar. When both an embedded version and `tool_version`
    are set, `tool_version` wins so an explicit --tool-version CLI
    override beats a pre-baked identifier in the project file.

    Args:
        config: Backend config dict (typically `pass_.config`).
        default_name: Tool name used when `tool` is unset.

    Returns:
        Identifier suitable for GBSConfig.get_tool().
    """
    tool = config.get("tool", default_name)
    version = config.get("tool_version")
    if version:
        base = tool.split('@', 1)[0]
        tool = f"{base}@{version}"
    return tool


class BasePass:
    """Base class for passes

    A Pass is PURE PLANNING METADATA. It does NOT execute build tools.
    Execution happens via Dispatchers after planning is complete.

    Subclasses must define class attributes:
    - name: Pass name (e.g., "ghdl-simulate")
    - input_types: Set of input file types
    - output_types: Set of output file types

    Optional class attributes:
    - can_fork: If True, planner may explore multiple paths (default: False)
    - priority: Planning priority, lower = preferred (default: 100)
    - types_with_library: File types requiring library classification (default: {"vhdl", "verilog"})

    Attributes:
        config: Backend-specific configuration
        project_config: Project-level configuration
        gbs_config: GBS configuration
    """

    # Class attributes (must be overridden by subclasses)
    name: str
    input_types: set[str]
    output_types: set[str]

    # Optional class attributes
    can_fork: bool = False
    priority: int = 100
    types_with_library: set[str] = {"vhdl", "verilog"}

    def __init__(self,
                 config: dict[str, Any],
                 project_config: dict[str, Any] | None = None,
                 gbs_config: GBSConfig | None = None):
        """Initialize pass

        Args:
            config: Backend-specific configuration
            project_config: Project-level configuration (optional)
            gbs_config: GBS configuration (optional)
        """
        self.config = config
        self.project_config = project_config or {}
        self.gbs_config = gbs_config

    def resolve_tool_identifier(self, default_name: str) -> str:
        """Combine backend `tool` and `tool_version` config into one identifier.

        See resolve_tool_identifier() below for the merge rule.
        """
        return resolve_tool_identifier(self.config, default_name)

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for source enumeration

        Passes can provide filter variables that will be merged with the
        OutputGroup's filter_vars before enumerating sources. This allows
        passes to request specific sources based on their needs.

        The planner combines filter_vars from ALL passes in a selected plan
        before enumerating sources. This ensures the source set matches
        what all passes expect.

        Default implementation returns empty dict. Override to provide
        custom filter variables.

        Returns:
            Dictionary of filter variable name -> value
        """
        return {}

    def dispatchers(self, context: BuildContext) -> list[Dispatcher]:
        """Create dispatchers for executing this pass transformations

        Default implementation returns empty list. Override to provide
        dispatcher instances.

        Args:
            context: Build context to pass to dispatcher constructors

        Returns:
            Dispatcher instance list
        """
        return []

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"name={self.name}, "
            f"input={self.input_types}, "
            f"output={self.output_types})"
        )

    def __str__(self) -> str:
        inputs = ", ".join(sorted(self.input_types))
        outputs = ", ".join(sorted(self.output_types))
        return f"{self.name}: [{inputs}] → [{outputs}]"
