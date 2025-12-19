"""Pass Metadata for Runtime Tracking

This module defines PassMetadata which wraps Pass instances for tracking
by the planner. The Pass protocol and base class are in gbs.protocol and gbs.base.
"""

from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocol import Pass

__all__ = ["PassMetadata"]


class PassMetadata:
    """Runtime metadata about a Pass instance

    Used by the planner to track pass selection and configuration.
    This is separate from the Pass class itself to keep Pass immutable.

    Attributes:
        pass_obj: The Pass instance
        config: Backend-specific configuration for this pass
        backend_name: Name of backend that provided this pass
    """

    def __init__(
        self,
        pass_obj: Pass,
        config: dict[str, Any],
        backend_name: str
    ):
        """Initialize pass metadata

        Args:
            pass_obj: The Pass object
            config: Backend configuration
            backend_name: Backend module name (e.g., "gbs.builtin.ghdl")
        """
        self.pass_obj = pass_obj
        self.config = config
        self.backend_name = backend_name

    def __eq__(self, other):
        return type(self.pass_obj) == type(other.pass_obj)

    @property
    def filter_vars(self) -> dict:
        return self.pass_obj.filter_vars()

    @property
    def name(self) -> str:
        """Pass name"""
        return self.pass_obj.name

    @property
    def input_types(self) -> set[str]:
        """Input file types"""
        return self.pass_obj.input_types

    @property
    def output_types(self) -> set[str]:
        """Output file types"""
        return self.pass_obj.output_types

    @property
    def types_with_library(self) -> set[str]:
        """File types that require library classification"""
        return self.pass_obj.types_with_library

    def __repr__(self) -> str:
        return (
            f"PassMetadata("
            f"pass={self.pass_obj}, "
            f"backend={self.backend_name})"
        )
