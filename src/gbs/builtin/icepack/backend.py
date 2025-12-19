"""icepack Backend implementation"""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from ...base import BasePass
from .passes import IcepackPass


class IcepackBackend(BaseBackend):
    """icepack Backend for Ice40 bitstream generation

    Converts ASCII bitstream (.asc) to binary bitstream (.bin)
    for Ice40 FPGAs.

    Configuration options:
        - icepack_tool: Tool identifier for lookup (default: "icepack")
    """

    def __init__(self):
        super().__init__("gbs.builtin.icepack")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list[Pass]:
        """Contribute icepack pass based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances that can help produce the outputs
        """
        passes = []

        # Contribute icepack pass if binary bitstream is needed
        if "ice40-bin" in output_types or "bitstream" in output_types or "ice40-bitstream" in output_types:
            passes.append(IcepackPass(config, project_config, gbs_config))

        return passes
