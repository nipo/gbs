"""Gowin Backend Plugin"""
from ...plugins import Plugin
from .backend import GowinBackend


class GowinPlugin(Plugin):
    """Gowin plugin providing FPGA synthesis and implementation backend"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.gowin",
            description="Gowin FPGA synthesis and implementation backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        """Return Gowin backend instance"""
        return [GowinBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return GowinPlugin()


# Legacy compatibility
def get_backend():
    """Legacy function for backwards compatibility"""
    return GowinBackend()


__all__ = ["GowinPlugin", "GowinBackend", "gbs_register", "get_backend"]

