"""yavs Backend Plugin"""
from ...base import BasePlugin
from .backend import YavsBackend


class YavsPlugin(BasePlugin):
    """yavs plugin providing VHDL simulation backend"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.yavs",
            description="yavs VHDL simulator backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        return [YavsBackend()]


def gbs_register():
    return YavsPlugin()


__all__ = ["YavsPlugin", "YavsBackend", "gbs_register"]
