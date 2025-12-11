"""Vivado Backend Plugin"""
from ...plugins import Plugin
from .backend import VivadoBackend


class VivadoPlugin(Plugin):
    """Vivado plugin providing FPGA synthesis and implementation backend"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.vivado",
            description="Xilinx Vivado FPGA synthesis and implementation backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return Vivado backend instance"""
        return [VivadoBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return VivadoPlugin()


__all__ = ["VivadoPlugin", "VivadoBackend", "gbs_register"]
