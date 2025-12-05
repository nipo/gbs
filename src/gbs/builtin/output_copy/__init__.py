"""Generic copy Backend Plugin"""
from ...plugins import Plugin
from .backend import OutputCopyBackend


class OutputCopyPlugin(Plugin):
    """Generic copy"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.output_copy",
            description="OutputCopy outputs from temporary build to desired path",
            version="1.0.0"
        )

    def enumerate_backends(self):
        return [OutputCopyBackend()]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return OutputCopyPlugin()


__all__ = ["OutputCopyPlugin", "OutputCopyBackend", "gbs_register"]
