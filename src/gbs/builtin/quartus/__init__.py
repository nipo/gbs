"""Altera Quartus Backend Plugin"""
from ...base import BasePlugin
from .backend import QuartusBackend


class QuartusPlugin(BasePlugin):
    """Altera Quartus plugin providing FPGA synthesis and implementation backend"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.quartus",
            description="Altera Quartus FPGA synthesis and implementation backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return Quartus backend instance"""
        return [QuartusBackend()]


def gbs_register():
    """Plugin registration function"""
    return QuartusPlugin()


__all__ = ["QuartusPlugin", "QuartusBackend", "gbs_register"]
