"""Memory Initialization Backend for GBS

This module implements a backend that generates memory initialization files.
This is a reference implementation demonstrating how a code generation backend works.
"""

from __future__ import annotations
from typing import Any

from ..model.dispatcher import BaseDispatcher, Dispatcher
from ..model.backend import BaseBackend
from ..model.passes import Pass
from ..model.build import BuildContext, BuildFileSet, BuildResource


# ========== Dispatcher (Execution) ==========

class MemInitDispatcher(BaseDispatcher):
    """Example dispatcher that generates memory initialization files

    This demonstrates a code generation dispatcher:
    - Finds memory specification files
    - Generates initialization files
    - Runs only once (idempotent)

    Priority: 150 (preprocessing/code generation)
    """

    def __init__(self):
        super().__init__("mem_init", priority=150)
        self._generated = False

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables"""
        return {
            "has_mem_init": True,
        }

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Generate memory initialization files"""
        if self._generated:
            return  # Only run once

        # Find memory spec files
        mem_specs = fileset.filter(file_type="mem_spec")

        if not mem_specs:
            self._generated = True
            return

        self.logger.info(f"Generating {len(mem_specs)} memory init files")

        for spec_br in mem_specs:
            # Generate VHDL initialization package
            init_path = spec_br.path.with_name(spec_br.path.stem + "_init.vhd")

            init_br = BuildResource(
                resource=context.get_resource(init_path),
                file_type="vhdl",
                library=spec_br.library,
                is_source=False,
                generated_by=self.name,
            )
            init_br.depends_on.add(spec_br)

            fileset.add(init_br)

            self.logger.debug(f"Generated {init_path.name} from {spec_br.path.name}")

        self._generated = True


# ========== Pass (Planning Metadata) ==========

class MemInitPass(Pass):
    """Pass that generates memory initialization files

    This demonstrates a code generation pass that creates VHDL
    initialization packages from memory specification files.
    Pure planning metadata - execution is handled by MemInitDispatcher.

    Input types: mem_spec
    Output types: vhdl
    """
    name = "mem-init-generate"
    input_types = {"mem_spec"}
    output_types = {"vhdl"}

    def contribute_filter_vars(self, config: dict[str, Any]) -> dict[str, Any]:
        """Indicate memory initialization support is available"""
        return {
            "has_mem_init": True,
        }


# ========== Backend (Planning Interface) ==========

class MemInitBackend(BaseBackend):
    """Backend providing memory initialization file generation

    This is a reference implementation demonstrating how a code generation backend works.
    """

    def __init__(self):
        super().__init__("gbs.backend.mem_init")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str]
    ) -> list[type[Pass]]:
        """Contribute memory initialization pass

        Args:
            config: Backend configuration (unused for this simple backend)
            output_types: Set of desired output types

        Returns:
            List with MemInitPass if vhdl is in output types
        """
        passes = []

        # If VHDL is desired and we have mem_spec sources, offer generation
        # Note: The planner will only use this pass if mem_spec sources exist
        if "vhdl" in output_types:
            passes.append(MemInitPass)

        return passes

    def create_dispatcher(self, config: dict[str, Any]) -> Dispatcher:
        """Create memory initialization dispatcher for execution

        Args:
            config: Backend configuration (unused for this simple backend)

        Returns:
            MemInitDispatcher instance
        """
        return MemInitDispatcher()


# ========== Registry Function ==========

def get_backend():
    """Get the memory initialization backend for build planning and execution

    Returns:
        MemInitBackend instance implementing the Backend Protocol
    """
    return MemInitBackend()
