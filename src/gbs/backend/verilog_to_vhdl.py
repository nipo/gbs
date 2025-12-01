"""Verilog to VHDL Transpiler Backend for GBS

This module implements a backend that transpiles Verilog files to VHDL.
This is a reference implementation demonstrating how a transpiler backend works.
"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ..model.backend import BaseBackend
from ..model.build import BuildContext, BuildFileSet, BuildResource


class VerilogToVHDLBackend(BaseBackend):
    """Example backend that transpiles Verilog to VHDL

    This is a reference implementation demonstrating how a transpiler backend works:
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
                language_version="2008",
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


# Pass-based backend implementation
def get_backend():
    """Get the pass-based backend for registry discovery"""
    from ..model.passes import Backend, Pass
    from ..logging import get_logger

    logger = get_logger(__name__)

    class VerilogToVHDLPass(Pass):
        """Pass that transpiles Verilog files to VHDL

        This is a reference implementation demonstrating a transformation pass.
        It simulates transpilation by creating VHDL files with the same structure
        as the input Verilog files.
        """
        name = "transpile"
        input_types = {"verilog"}
        output_types = {"vhdl"}

        def contribute_filter_vars(self, config):
            """Indicate VHDL is the target language"""
            return {
                "target_language": "vhdl",
                "has_verilog_transpiler": True,
            }

        async def execute(self, context, inputs):
            """Transform Verilog BuildResources to VHDL BuildResources

            Args:
                context: BuildContext for resource management
                inputs: List of BuildResource objects with file_type="verilog"

            Returns:
                List of BuildResource objects with file_type="vhdl"
            """
            outputs = []

            logger.info(f"Transpiling {len(inputs)} Verilog files to VHDL")

            for verilog_br in inputs:
                # Generate VHDL file path
                vhdl_path = verilog_br.path.with_suffix(".vhd")

                # Create VHDL BuildResource
                vhdl_br = BuildResource(
                    resource=context.get_resource(vhdl_path),
                    file_type="vhdl",
                    library=verilog_br.library,
                    language_version="2008",
                    is_source=False,
                    generated_by="gbs.backend.verilog_to_vhdl:transpile",
                )

                # Copy dependencies from original
                vhdl_br.depends_on = verilog_br.depends_on.copy()

                outputs.append(vhdl_br)

                logger.debug(
                    f"Transpiled {verilog_br.path.name} -> {vhdl_path.name}"
                )

            return outputs

    class VerilogToVHDLBackend(Backend):
        """Backend providing Verilog to VHDL transpilation"""
        passes = [VerilogToVHDLPass]

    return VerilogToVHDLBackend
