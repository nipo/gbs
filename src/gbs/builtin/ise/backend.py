"""Xilinx ISE Backend implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...base import BaseBackend
from ...base import BasePass
from .passes import IseSynthesizePass

class IseBackend(BaseBackend):
    """Xilinx ISE FPGA synthesis backend

    Provides ISE synthesis pass and dispatcher for executing FPGA builds.
    """

    def __init__(self):
        super().__init__("gbs.builtin.ise")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list[Pass]:
        """Contribute ISE passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances that can help produce the outputs
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
                self.warning("ISE backend skipped: no part selected")
                return []
            passes.append(IseSynthesizePass(config, project_config, gbs_config))

        return passes
