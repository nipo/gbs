"""Xilinx ISE Backend implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.protocol import BaseBackend
from ...backend.dispatcher import Dispatcher
from ...planner.passes import Pass
from .passes import IseSynthesizePass
from .dispatcher import IseDispatcher


class IseBackend(BaseBackend):
    """Xilinx ISE FPGA synthesis backend

    Provides ISE synthesis pass and dispatcher for executing FPGA builds.

    Configuration options:
        - output_dir: Build output directory
        - output_base_name: Base name for output files
    """

    def __init__(self):
        super().__init__("gbs.builtin.ise")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str]
    ) -> list[type[Pass]]:
        """Contribute ISE passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types

        Returns:
            List of Pass classes that can help produce the outputs
        """
        passes = []

        # If any output type is ISE-related, contribute the ISE
        # synthesis pass
        ise_types = {
            "ise-bitstream", "ise-timing-report", "ise-netlist",
            "ise-netlist-functional", "ise-netlist-partial", "ise-netlist-full"
        }
        if output_types & ise_types:
            target = config.get("target", {})
            part = target.get("part")
            if not part:
                self.logger.warning("ISE backend skipped: no part selected")
                return []
            passes.append(IseSynthesizePass)

        return passes

    def create_dispatcher(self, config: dict[str, Any]) -> Dispatcher:
        """Create ISE dispatcher for execution

        Args:
            config: Backend configuration with optional:
                - output_dir: Output directory path
                - output_base_name: Base name for outputs

        Returns:
            IseDispatcher instance
        """
        output_dir = config.get("output_dir", "ise-build")
        tool = config.get("tool", "ise")
        output_base_name = config.get("output_base_name")
        target = config["target"]

        return IseDispatcher(
            output_dir=output_dir,
            output_base_name=output_base_name,
            target=target,
            tool = tool,
        )
