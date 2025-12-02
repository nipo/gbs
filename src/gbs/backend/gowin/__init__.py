"""Gowin FPGA Synthesis Backend for GBS

This module implements the Gowin synthesis backend that:
1. Synthesizes VHDL/Verilog to netlist (via gw_sh)
2. Aggregates constraints from multiple sources
3. Runs place & route to generate bitstream (via gw_sh)
"""

from .backend import GowinDispatcher
# Backward compatibility alias
GowinBackend = GowinDispatcher

# Stub for new pass-based registry (Phase 2 compatibility)  
def get_backend():
    """Stub backend for registry discovery"""
    from ...model.passes import Backend, Pass
    
    class GowinStubPass(Pass):
        name = "gowin_stub"
        input_types = {"vhdl", "verilog"}
        output_types = {"gowin-fs", "gowin-bin"}
        
        async def execute(self, context, inputs):
            return []
    
    class GowinBackend(Backend):
        passes = [GowinStubPass]
    
    return GowinBackend
