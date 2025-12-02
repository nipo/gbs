"""Verilog to VHDL Transpiler Backend for GBS

This module implements a backend that transpiles Verilog files to VHDL.
This is a reference implementation demonstrating how a transpiler backend works.
"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ..model.dispatcher import BaseDispatcher, Dispatcher
from ..model.backend import BaseBackend
from ..model.passes import Pass
from ..model.build import BuildContext, BuildFileSet, BuildResource


# ========== Dispatcher (Execution) ==========

class VerilogToVHDLDispatcher(BaseDispatcher):
    """Example dispatcher that transpiles Verilog to VHDL

    This is a reference implementation demonstrating how a transpiler dispatcher works:
    - Finds Verilog files in the fileset
    - Creates VHDL equivalents (simulated - doesn't actually transpile)
    - Removes Verilog files
    - Adds generated VHDL files

    Priority: 200 (preprocessing/transpilation)
    """

    def __init__(self):
        super().__init__("verilog_to_vhdl", priority=200)
        self._processed_files: set[Path] = set()

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables indicating VHDL is the target"""
        return {
            "target_language": "vhdl",
            "has_verilog_transpiler": True,
        }

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Transpile all Verilog files to VHDL"""
        # Find all Verilog files we haven't processed yet
        verilog_files = [
            br for br in fileset.filter(file_type="verilog")
            if br.path not in self._processed_files
        ]

        if not verilog_files:
            return  # Nothing to do

        self.logger.info(f"Transpiling {len(verilog_files)} Verilog files to VHDL")

        for verilog_br in verilog_files:
            # Generate VHDL file path
            vhdl_path = verilog_br.path.with_suffix(".vhd")

            # Create VHDL BuildResource
            vhdl_br = BuildResource(
                resource=context.get_resource(vhdl_path),
                file_type="vhdl",
                library=verilog_br.library,
                file_type_version="2008",
                is_source=False,
                generated_by=self.name,
            )

            # Copy dependencies
            vhdl_br.depends_on = verilog_br.depends_on.copy()

            # Replace Verilog with VHDL
            fileset.replace(verilog_br.path, vhdl_br, transfer_dependencies=True)

            # Mark as processed
            self._processed_files.add(verilog_br.path)

            self.logger.debug(f"Transpiled {verilog_br.path.name} -> {vhdl_path.name}")


# ========== Pass (Planning Metadata) ==========

class VerilogToVHDLPass(Pass):
    """Pass that transpiles Verilog files to VHDL

    This is a reference implementation demonstrating a transformation pass.
    Pure planning metadata - execution is handled by VerilogToVHDLDispatcher.

    Input types: verilog
    Output types: vhdl
    """
    name = "verilog-to-vhdl"
    input_types = {"verilog"}
    output_types = {"vhdl"}

    def contribute_filter_vars(self, config: dict[str, Any]) -> dict[str, Any]:
        """Indicate VHDL is the target language"""
        return {
            "target_language": "vhdl",
            "has_verilog_transpiler": True,
        }


# ========== Backend (Planning Interface) ==========

class VerilogToVHDLBackend(BaseBackend):
    """Backend providing Verilog to VHDL transpilation

    This is a reference implementation demonstrating how a transpiler backend works.
    """

    def __init__(self):
        super().__init__("gbs.backend.verilog_to_vhdl")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str]
    ) -> list[type[Pass]]:
        """Contribute transpilation pass

        Args:
            config: Backend configuration (unused for this simple backend)
            output_types: Set of desired output types

        Returns:
            List with VerilogToVHDLPass if vhdl is in output types
        """
        passes = []

        # If VHDL is desired and we have verilog sources, offer transpilation
        # Note: The planner will only use this pass if verilog sources exist
        if "vhdl" in output_types:
            passes.append(VerilogToVHDLPass)

        return passes

    def create_dispatcher(self, config: dict[str, Any]) -> Dispatcher:
        """Create transpiler dispatcher for execution

        Args:
            config: Backend configuration (unused for this simple backend)

        Returns:
            VerilogToVHDLDispatcher instance
        """
        return VerilogToVHDLDispatcher()


# ========== Registry Function ==========

def get_backend():
    """Get the Verilog to VHDL backend for build planning and execution

    Returns:
        VerilogToVHDLBackend instance implementing the Backend Protocol
    """
    return VerilogToVHDLBackend()
