"""Compression Plugin

Provides a generic dispatcher that compresses files in the build fileset
based on type suffixes like "+gzip".
"""
from ...plugins import Plugin


class CompressPlugin(Plugin):
    """Compression plugin for compressing build outputs"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.compress",
            description="Compress build outputs using type suffixes",
            version="1.0.0"
        )

    def generic_dispatchers(self):
        """Return compression dispatcher"""
        from .dispatcher import CompressDispatcher
        return [CompressDispatcher()]


def gbs_register():
    """Plugin registration function"""
    return CompressPlugin()


__all__ = ["gbs_register"]
