"""NVC Backend implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...base import BaseBackend
from ...base import BasePass
from .passes import NVCSimulatePass

class NVCBackend(BaseBackend):
    """NVC Backend for VHDL simulation

    Provides NVC simulation pass and dispatcher for executing VHDL builds.

    Configuration options:
        - vhdl_standard: VHDL standard (e.g., "1993", "2000", "2008", "2019")
        - nvc_tool: Tool identifier for lookup (default: "nvc")
    """

    def __init__(self):
        super().__init__("gbs.builtin.nvc")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list[Pass]:
        """Contribute NVC passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances that can help produce the outputs
        """
        passes = []

        # If any output type is nvc-simulator or a generic simulator type,
        # contribute the NVC simulation pass
        if "nvc-simulator" in output_types or "simulator" in output_types:
            passes.append(NVCSimulatePass(config, project_config, gbs_config))

        return passes
