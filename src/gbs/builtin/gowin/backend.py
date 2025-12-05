"""Gowin Backend implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.protocol import BaseBackend
from ...planner.passes import Pass
from .passes import GowinSynthesizePass

class GowinBackend(BaseBackend):
    """Gowin FPGA synthesis backend

    Provides Gowin synthesis pass and dispatcher for executing FPGA builds.

    Configuration options:
        - device: Target device (e.g., "GW1NR-9")
        - gowin_tool: Tool identifier for lookup (default: "gowin")
    """

    def __init__(self):
        super().__init__("gbs.builtin.gowin")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str]
    ) -> list[Pass]:
        """Contribute Gowin passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types

        Returns:
            List of Pass instances that can help produce the outputs
        """
        passes = []

        if any(t in output_types for t in ["gowin-fs"]):
            passes.append(GowinSynthesizePass(config))

        return passes
