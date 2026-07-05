"""openxc7 Backend implementation."""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend, BasePass
from .passes import Openxc7Pass


class Openxc7Backend(BaseBackend):
    """Series-7 bitstream generator combining fasm2frames and xc7frames2bit.

    Configuration:
        - fasm2frames_tool: tool identifier for fasm2frames (default "fasm2frames")
        - xc7frames2bit_tool: tool identifier for xc7frames2bit (default "xc7frames2bit")
        - target.part: Xilinx part in vivado form, e.g. "xc7a35t-1cpg236"
    """

    def __init__(self):
        super().__init__("gbs.builtin.openxc7")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None,
    ) -> list[BasePass]:
        if "xilinx-bitstream" in output_types:
            return [Openxc7Pass(config, project_config, gbs_config)]
        return []
