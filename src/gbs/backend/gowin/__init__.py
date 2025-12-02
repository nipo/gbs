"""Gowin FPGA Synthesis Backend for GBS

This module implements the Gowin synthesis backend that:
1. Synthesizes VHDL/Verilog to netlist (via gw_sh)
2. Aggregates constraints from multiple sources
3. Runs place & route to generate bitstream (via gw_sh)
"""

from .dispatcher import GowinDispatcher
from .backend import GowinBackend


def get_backend():
    """Get the Gowin backend for build planning and execution

    Returns:
        GowinBackend instance implementing the Backend Protocol
    """
    return GowinBackend()
