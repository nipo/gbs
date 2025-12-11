"""QuestaSim/ModelSim Backend Plugin"""

from ...plugins import Plugin
from .backend import QuestaBackend


class QuestaPlugin(Plugin):
    """QuestaSim/ModelSim simulation backend plugin"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.questa",
            description="Siemens QuestaSim/ModelSim VHDL/Verilog simulation backend",
            version="1.0.0"
        )

    def enumerate_backends(self):
        return [QuestaBackend()]


def gbs_register():
    """Register this plugin with GBS"""
    return QuestaPlugin()


__all__ = ["QuestaPlugin", "QuestaBackend", "gbs_register"]
