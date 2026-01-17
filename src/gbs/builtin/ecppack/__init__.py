"""ecppack bitstream generation backend plugin"""

from ...base import BasePlugin
from .backend import EcppackBackend


class EcppackPlugin(BasePlugin):
    """ecppack plugin providing ECP5 bitstream generation"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.ecppack",
            description="ecppack ECP5 bitstream generator",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return ecppack backend instance"""
        return [EcppackBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return EcppackPlugin()


__all__ = ["EcppackPlugin", "EcppackBackend", "gbs_register"]
