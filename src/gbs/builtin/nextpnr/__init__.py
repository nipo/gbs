"""nextpnr place-and-route backend plugin"""

from ...base import BasePlugin
from .backend import NextpnrBackend


class NextpnrPlugin(BasePlugin):
    """nextpnr plugin providing FPGA place-and-route backend"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.nextpnr",
            description="nextpnr FPGA place-and-route backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return nextpnr backend instance"""
        return [NextpnrBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return NextpnrPlugin()


__all__ = ["NextpnrPlugin", "NextpnrBackend", "gbs_register"]
