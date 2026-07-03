"""Lattice Diamond Backend Plugin"""
from ...base import BasePlugin
from .backend import DiamondBackend


class DiamondPlugin(BasePlugin):
    """Lattice Diamond plugin providing FPGA synthesis and implementation backend"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.diamond",
            description="Lattice Diamond FPGA synthesis and implementation backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return Diamond backend instance"""
        return [DiamondBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return DiamondPlugin()


__all__ = ["DiamondPlugin", "DiamondBackend", "gbs_register"]
