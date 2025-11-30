"""Memory Initialization Backend for GBS

This module implements a backend that generates memory initialization files.
This is a reference implementation demonstrating how a code generation backend works.
"""

from __future__ import annotations
from typing import Any

from ..model.backend import BaseBackend
from ..model.build import BuildContext, BuildFileSet, BuildResource


class MemInitBackend(BaseBackend):
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
