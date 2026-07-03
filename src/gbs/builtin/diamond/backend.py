"""Lattice Diamond Backend implementation"""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from .device import DiamondPart
from .passes import DiamondEcp5Pass


class DiamondBackend(BaseBackend):
    """Lattice Diamond FPGA synthesis backend

    Provides Diamond synthesis passes and dispatcher for executing
    FPGA builds through the diamondc Tcl console.

    The target part must be a full Diamond part number (the ordering
    code shown in the Diamond device selector), e.g. LFE5U-25F-6BG256C.
    """

    def __init__(self):
        super().__init__("gbs.builtin.diamond")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list[Pass]:
        """Contribute Diamond passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances that can help produce the outputs
        """
        if not (output_types & DiamondEcp5Pass.output_types):
            return []

        target = config.get("target", {})
        part_str = target.get("part")
        if not part_str:
            self.logger.debug("Diamond backend skipped: no part selected")
            return []

        part = DiamondPart.ecp5_parse(part_str)
        if not part:
            # Not a Diamond ECP5 part number; another backend
            # (e.g. nextpnr/ecppack) probably owns this target
            self.logger.debug(f"Diamond backend skipped: {part_str} is not a Diamond ECP5 part number")
            return []

        return [DiamondEcp5Pass(part, config, project_config, gbs_config)]
