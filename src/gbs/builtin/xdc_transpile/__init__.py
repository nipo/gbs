"""Vivado XDC to nextpnr-xilinx constraint transpiler plugin."""

from ...base import BasePlugin
from .backend import XdcTranspileBackend


class XdcTranspilePlugin(BasePlugin):
    """Plugin providing Vivado XDC to nextpnr-xilinx constraint translation."""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.xdc_transpile",
            description="Translate Vivado XDC to nextpnr-xilinx constraints",
            version="1.0.0",
        )

    def enumerate_backends(self):
        return [XdcTranspileBackend()]


def gbs_register():
    """Plugin registration entry point."""
    return XdcTranspilePlugin()


__all__ = ["XdcTranspilePlugin", "XdcTranspileBackend", "gbs_register"]
