"""Vivado Dispatcher

Dispatcher implementation for Xilinx Vivado FPGA synthesis flow
in non-project (in-memory) mode.
"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...build.context import BuildContext
from ...build.task import ResourceTypology
from .base import VivadoDispatcherBase
from .task import NonProjectBuild, AggregateSynthesisReport, AggregatePnrReport


# Accepted input file types
ACCEPTED_INPUT_TYPES = {
    "vhdl",
    "verilog",
    "xilinx-xci",
    "xilinx-xdc",
    "xilinx-constraints-tcl",
    "vivado-block-design",
    "vivado-init-tcl",
    "vivado-ip-zip",
    "vivado-bus-definition",
    "vivado-ip-repository",
}

# Output file types and their default filenames. `bitstream` is the
# canonical shared name; get_resource() auto-aliases it to
# `vivado-bitstream`, `xilinx-bitstream`, ... so both legacy names and
# the flattened name resolve to the file Vivado just wrote.
OUTPUT_FILES = {
    "vivado-routing-report": "routing-report.txt",
    "vivado-timing-report": "timing-report.txt",
    "vivado-power-report": "power-report.txt",
    "vivado-usage-report": "usage-report.txt",
    "vivado-netlist-edif": "pnr-netlist.edif",
    "vivado-drc-report": "drc-report.txt",
    "bitstream": "final.bit",
}


class VivadoDispatcher(VivadoDispatcherBase):
    """Vivado FPGA synthesis backend (non-project mode)

    Workflow:
      First process() call:
        - Creates single build task with no inputs declared
        - Attaches output resources for all output types

      Subsequent process() calls:
        - Attaches any pending files of accepted input types to the task
        - HDL files are added in library dependency order
    """

    def __init__(
        self,
        context: BuildContext,
        vhdl_std: str = "2008",
        vivado_tool: str = "vivado",
        target: dict[str, str] | None = None,
    ):
        super().__init__(
            context,
            "vivado",
            vhdl_std=vhdl_std,
            vivado_tool=vivado_tool,
            target=target,
        )
        self._build_task: NonProjectBuild | None = None

    async def process(self) -> None:
        """Process input files

        On first call, creates the build task with output resources.
        On every call, attaches any pending files of accepted types to the task.
        """
        if self._build_task is None:
            await self._create_build_task()

        # Attach any pending files of accepted types
        await self._attach_pending_inputs()

    async def _create_build_task(self) -> None:
        """Create the single build task with output resources"""
        session = self.session_get()
        part = self.target.get("part")

        if not part:
            raise RuntimeError("Vivado backend requires 'part' in target configuration")

        # Create output resources
        outputs = []
        for file_type, filename in OUTPUT_FILES.items():
            path = self.context.output_path / filename
            resource = self.context.get_resource(
                path,
                file_type=file_type,
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name,
            )
            outputs.append(resource)

        # Create the build task with no inputs (inputs added dynamically)
        self._build_task = NonProjectBuild(
            dispatcher=self,
            session=session,
            part=part,
            inputs = [],
            outputs=outputs,
        )

        self.attach_definition_dependencies(self._build_task)

        self.info(f"Created Vivado build task for part {part}")

        # Create report aggregation tasks for any requested aggregate outputs
        # Map intermediate report resources by type for lookup
        intermediate_by_type = {r.file_type: r for r in outputs}

        for dest in self.context.filter_pending(file_type="vivado-synthesis-report"):
            inputs = [intermediate_by_type[t] for t in AggregateSynthesisReport.REPORT_TYPES
                       if t in intermediate_by_type]
            AggregateSynthesisReport(
                dispatcher=self,
                inputs=inputs,
                outputs=[dest],
            )

        for dest in self.context.filter_pending(file_type="vivado-pnr-report"):
            inputs = [intermediate_by_type[t] for t in AggregatePnrReport.REPORT_TYPES
                       if t in intermediate_by_type]
            AggregatePnrReport(
                dispatcher=self,
                inputs=inputs,
                outputs=[dest],
            )

    async def _attach_pending_inputs(self) -> None:
        """Attach any pending files of accepted types to the build task"""
        existing_paths = {r.path for r in self._build_task.inputs}

        for library, resources in self.context.get_pending_by_library_ordered():
            for source in resources:
                if source.file_type not in ACCEPTED_INPUT_TYPES:
                    continue
                if source.path in existing_paths:
                    continue

                resource = self.context.get_resource(source.path)
                resource.metadata = {
                    'file_type': source.file_type,
                    'library': source.library,
                    'variant': getattr(source, 'variant', None),
                }

                self._build_task.add_input(resource)
