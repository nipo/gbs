"""Output Copy Backend Plugin

This backend copies files from the build fileset to the output paths
specified in the project configuration.
"""
from ...plugins import Plugin

class OutputCopyPlugin(Plugin):
    """Output copy plugin for extracting build outputs"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.output_copy",
            description="Copy build outputs to specified paths",
            version="1.0.0"
        )

    def generic_dispatchers(self, context):
        """Return OutputCopy backend instance

        Args:
            context: Build context to pass to dispatcher constructor
        """
        from .dispatcher import OutputCopyDispatcher
        return [OutputCopyDispatcher(context)]


def gbs_register():
    """Plugin registration function

    Called by the plugin system during discovery.
    Must return one or more Plugin instances.
    """
    return OutputCopyPlugin()


__all__ = ["gbs_register"]
