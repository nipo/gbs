"""ecppack Backend implementation"""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from ...base import BasePass
from .passes import EcppackPass


class EcppackBackend(BaseBackend):
    """ecppack Backend for ECP5 bitstream generation

    Converts text config (.config) to binary bitstream (.bit)
    for ECP5 FPGAs.

    Configuration options:
        - ecppack_tool: Tool identifier for lookup (default: "ecppack")
    """

    def __init__(self):
        super().__init__("gbs.builtin.ecppack")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list[Pass]:
        """Contribute ecppack pass based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances that can help produce the outputs
        """
        passes = []

        # Contribute ecppack pass if binary bitstream is needed
        if "ecp5-bit" in output_types or "ecp5-bitstream" in output_types:
            passes.append(EcppackPass(config, project_config, gbs_config))

        return passes
