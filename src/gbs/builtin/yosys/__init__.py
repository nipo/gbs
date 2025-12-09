"""Yosys synthesis backend plugin"""

from ...plugins import Plugin
from .backend import YosysBackend


class YosysPlugin(Plugin):
    """Yosys plugin providing FPGA synthesis backend"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.yosys",
            description="Yosys FPGA synthesis backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return Yosys backend instance"""
        return [YosysBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return YosysPlugin()


__all__ = ["YosysPlugin", "YosysBackend", "gbs_register"]
