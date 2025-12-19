"""GHDL Backend Plugin"""
from ...base import BasePlugin
from .backend import GHDLBackend


class GHDLPlugin(BasePlugin):
    """GHDL plugin providing VHDL simulation backend"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.ghdl",
            description="GHDL VHDL simulator backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return GHDL backend instance"""
        return [GHDLBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return GHDLPlugin()


__all__ = ["GHDLPlugin", "GHDLBackend", "gbs_register"]

