"""Memory Initialization Backend for GBS

This module implements a backend that generates memory initialization files.
This is a reference implementation demonstrating how a code generation backend works.
"""

from __future__ import annotations
from typing import Any

from ...model.dispatcher import BaseDispatcher
from ..model.build import BuildContext, BuildFileSet, BuildResource


class MemInitDispatcher(BaseDispatcher):
    """Example backend that generates memory initialization files

    This demonstrates a code generation backend:
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


# Pass-based backend implementation
def get_backend():
    """Get the pass-based backend for registry discovery"""
    from ..model.passes import Backend, Pass
    from ..logging import get_logger

    logger = get_logger(__name__)

    class MemInitPass(Pass):
        """Pass that generates memory initialization files

        This demonstrates a code generation pass that creates VHDL
        initialization packages from memory specification files.
        """
        name = "generate"
        input_types = {"mem_spec"}
        output_types = {"vhdl"}

        def contribute_filter_vars(self, config):
            """Indicate memory initialization support is available"""
            return {
                "has_mem_init": True,
            }

        async def execute(self, context, inputs):
            """Generate VHDL initialization files from memory specs

            Args:
                context: BuildContext for resource management
                inputs: List of BuildResource objects with file_type="mem_spec"

            Returns:
                List of BuildResource objects with file_type="vhdl"
            """
            outputs = []

            logger.info(f"Generating {len(inputs)} memory init files")

            for spec_br in inputs:
                # Generate VHDL initialization package
                init_path = spec_br.path.with_name(spec_br.path.stem + "_init.vhd")

                init_br = BuildResource(
                    resource=context.get_resource(init_path),
                    file_type="vhdl",
                    library=spec_br.library,
                    is_source=False,
                    generated_by="gbs.backend.mem_init:generate",
                )
                # Initialization file depends on the spec file
                init_br.depends_on.add(spec_br)

                outputs.append(init_br)

                logger.debug(
                    f"Generated {init_path.name} from {spec_br.path.name}"
                )

            return outputs

    class MemInitBackend(Backend):
        """Backend providing memory initialization file generation"""
        passes = [MemInitPass]

    return MemInitBackend
