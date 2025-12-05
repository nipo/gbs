"""Xilinx ISE Backend Plugin"""
from ...plugins import Plugin
from .backend import IseBackend


class IsePlugin(Plugin):
    """Xilinx ISE plugin providing FPGA synthesis and implementation backend"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.ise",
            description="Xilinx ISE FPGA synthesis and implementation backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return ISE backend instance"""
        return [IseBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return IsePlugin()


__all__ = ["IsePlugin", "IseBackend", "gbs_register"]
