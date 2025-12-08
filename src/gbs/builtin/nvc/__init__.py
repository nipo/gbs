"""NVC Backend Plugin"""
from ...plugins import Plugin
from .backend import NVCBackend


class NVCPlugin(Plugin):
    """NVC plugin providing VHDL simulation backend"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.nvc",
            description="NVC VHDL simulator backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return NVC backend instance"""
        return [NVCBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return NVCPlugin()


__all__ = ["NVCPlugin", "NVCBackend", "gbs_register"]
