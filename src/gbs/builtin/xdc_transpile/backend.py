"""XDC transpile backend."""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from .passes import XdcTranspilePass


class XdcTranspileBackend(BaseBackend):
    """Backend that translates Vivado XDC into nextpnr-xilinx constraints.

    Contributes its pass when a ``nextpnr-xdc`` file is requested, which
    the nextpnr-xilinx place-and-route pass asks for as its constraint
    input.
    """

    def __init__(self):
        super().__init__("gbs.builtin.xdc_transpile")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: "GBSConfig | None" = None,
    ) -> list["Pass"]:
        if "nextpnr-xdc" not in output_types:
            return []
        return [XdcTranspilePass(config, project_config, gbs_config)]
