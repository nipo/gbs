"""Compression Plugin

Provides a generic dispatcher that compresses files in the build fileset
based on type suffixes like "+gzip".
"""
from ...base import BasePlugin


class CompressPlugin(BasePlugin):
    """Compression plugin for compressing build outputs"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.compress",
            description="Compress build outputs using type suffixes",
            version="1.0.0"
        )

    def generic_dispatchers(self, context):
        """Return compression dispatcher

        Args:
            context: Build context to pass to dispatcher constructor
        """
        from .dispatcher import CompressDispatcher
        return [CompressDispatcher(context)]


def gbs_register():
    """Plugin registration function"""
    return CompressPlugin()


__all__ = ["gbs_register"]
