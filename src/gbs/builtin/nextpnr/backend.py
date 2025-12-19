"""nextpnr Backend implementation"""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from ...base import BasePass
from .passes import NextpnrIce40Pass


class NextpnrBackend(BaseBackend):
    """nextpnr Backend for FPGA place-and-route

    Provides place-and-route passes for various FPGA targets.
    Currently supports:
    - Ice40 (via nextpnr-ice40)

    Configuration options:
        - nextpnr_tool: Tool identifier for lookup (default: "nextpnr-ice40")
        - package: FPGA package (e.g., "hx1k", "up5k")
        - pcf: Path to PCF constraint file (optional)
    """

    def __init__(self):
        super().__init__("gbs.builtin.nextpnr")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list[Pass]:
        """Contribute nextpnr passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances that can help produce the outputs
        """
        passes = []

        # Contribute ice40 PnR pass if ice40 bitstream is needed
        if "ice40-asc" in output_types:
            passes.append(NextpnrIce40Pass(config, project_config, gbs_config))

        return passes
