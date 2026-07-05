"""openxc7 bitstream backend.

Turns nextpnr-xilinx FASM output into a Xilinx Series-7 .bit file
by chaining fasm2frames (FASM -> frame deltas) and xc7frames2bit
(frames -> bitstream). Both tools ship in the openxc7 apio package.
"""

from ...base import BasePlugin
from .backend import Openxc7Backend


class Openxc7Plugin(BasePlugin):
    def __init__(self):
        super().__init__(
            name="gbs.builtin.openxc7",
            description="openxc7 Series-7 bitstream generator (fasm2frames + xc7frames2bit)",
            version="1.0.0",
        )

    def enumerate_backends(self):
        return [Openxc7Backend()]


def gbs_register():
    return Openxc7Plugin()


__all__ = ["Openxc7Plugin", "Openxc7Backend", "gbs_register"]
