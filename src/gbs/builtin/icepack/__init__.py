"""icepack bitstream generation backend plugin"""

from ...plugins import Plugin
from .backend import IcepackBackend


class IcepackPlugin(Plugin):
    """icepack plugin providing Ice40 bitstream generation"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.icepack",
            description="icepack Ice40 bitstream generator",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return icepack backend instance"""
        return [IcepackBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return IcepackPlugin()


__all__ = ["IcepackPlugin", "IcepackBackend", "gbs_register"]
