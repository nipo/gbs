"""Vivado IP Packaging Backend Plugin"""
from ...base import BasePlugin
from .backend import VivadoIpBackend


class VivadoIpPlugin(BasePlugin):
    """Vivado IP packaging plugin for creating IP-XACT packages"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.vivado-ip",
            description="Xilinx Vivado IP packaging backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return Vivado IP backend instance"""
        return [VivadoIpBackend()]


def gbs_register():
    """Plugin registration function"""
    return VivadoIpPlugin()


__all__ = ["VivadoIpPlugin", "VivadoIpBackend", "gbs_register"]
