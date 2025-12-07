"""Xilinx ISE Dispatcher implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from . import task


class IseDispatcher(BaseDispatcher):
    """Xilinx ISE FPGA synthesis backend

    Workflow (all tasks created on first process() call with HDL sources):

    1. XST (Synthesis):
       - Creates .prj file listing all sources
       - Creates .xst command file
       - Runs xst to produce .ngc netlist

    2. NGDBUILD:
       - Takes .ngc and .ucf constraints
       - Produces .ngd (native generic database)

    3. MAP:
       - Takes .ngd
       - Produces .map.ncd and .pcf

    4. PAR (Place and Route):
       - Takes .map.ncd
       - Produces .par.ncd

    5. TRCE (Timing Report):
       - Takes .par.ncd and .pcf
       - Produces .twr timing report

    6. BITGEN:
       - Takes .par.ncd
       - Produces .bit bitstream

    Priority: 600 (synthesis/backend compilation)
    """

    def __init__(
        self,
        tool: str,
        target: dict[str, str] = {},
    ):
        super().__init__("ise", priority=600)
        self.output_base_name = "project"
        self.target = target
        self.device = target["part"]
        self.tool = tool

        self.xst_task = None
        self.bmm_task = None
        self.net_task = None
        self.map_task = None
        self.par_task = None
        self.trce_task = None
        self.bitgen_task = None

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables for ISE synthesis

        Returns:
            Filter variables for synthesis targeting
        """
        return {
            "target-usage": "synthesis",
            "vendor": "xilinx",
        }

    async def process(
        self,
        context: BuildContext
    ) -> None:
        """Process HDL sources and constraints

        Creates all tasks on first call with HDL sources.
        """

        # Get target from project config
        if not context.project:
            raise ValueError("No project configured")

        # Get output base name
        output_base_name = self.output_base_name or context.get_topcell()

        if not self.xst_task:
            await self._task_graph_create(context)

        await self._sources_hookup(context)

    async def _sources_hookup(
        self,
        context: BuildContext,
    ) -> None:
        # Add HDL input resources
        sources = list(context.filter_pending(file_type=["vhdl", "verilog"]))
        if sources:
            self.logger.debug(f"Adding {len(sources)} XST sources")
        for resource in sources:
            dependents = context.remove_pending(resource.path)
            for dep in dependents:
                self.xst_task.dependency_add(dep)

            self.xst_task.inputs.append(resource)
            self.xst_task.dependency_add(resource)

        # Add UCF input resources
        sources = list(context.filter_pending(file_type=["xilinx-ucf"]))
        if sources:
            self.logger.debug(f"Adding {len(sources)} UCF sources")
        for resource in sources:
            dependents = context.remove_pending(resource.path)
            for dep in dependents:
                self.net_task.dependency_add(dep)

            self.logger.debug(f"Adding netlister UCF: {resource}")
            self.net_task.inputs.append(resource)
            self.net_task.dependency_add(resource)

    async def _task_graph_create(
        self,
        context: BuildContext,
    ) -> None:
        """Create all ISE build tasks

        Creates the full chain: XST -> NGDBUILD -> MAP -> PAR -> TRCE -> BITGEN
        """
        config = context.get_tool(self.tool)
        ise_path = Path(config["path"])

        env_resource = context.get_resource(
            ise_path / "ISE_DS" / "settings64.sh",
            file_type="ise-settings-sh",
            typology=ResourceTypology.SOURCE
        )

        # Define intermediate file paths
        ngc_path = context.output_path / "syn" / f"project.ngc"
        edif_path = context.output_path / "syn" / f"project.edif"
        bmm_path = context.output_path / "func" / f"project.bmm"
        ngd_path = context.output_path / "func" / f"project.ngd"
        map_path = context.output_path / "map" / f"project.map.ncd"
        pcf_path = context.output_path / "map" / f"project.map.pcf"
        par_path = context.output_path / "par" / f"project.par.ncd"
        twr_path = context.output_path / "par" / f"project.twr"
        bit_path = context.output_path / f"project.bit"

        # Create resources for all files
        ngc_resource = context.get_resource(
            ngc_path,
            file_type="ise-netlist-xst",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )
        edif_resource = context.get_resource(
            edif_path,
            file_type="ise-netlist",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )
        bmm_resource = context.get_resource(
            bmm_path,
            file_type="ise-bmm",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )
        ngd_resource = context.get_resource(
            ngd_path,
            file_type="ise-netlist-functional",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )
        map_resource = context.get_resource(
            map_path,
            file_type="ise-netlist-partial",
            library="work",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )
        pcf_resource = context.get_resource(
            pcf_path,
            file_type="ise-physical-constraints",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )
        par_resource = context.get_resource(
            par_path,
            file_type="ise-netlist-full",
            library="work",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )
        twr_resource = context.get_resource(
            twr_path,
            file_type="ise-timing-report",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )
        bit_resource = context.get_resource(
            bit_path,
            file_type="ise-bitstream",
            typology=ResourceTypology.OUTPUT,
            generated_by=self.name
        )

        # Run XST synthesis
        self.xst_task = task.Xst(
            context=context,
            device=self.device,
            inputs=[env_resource],
            outputs=[ngc_resource],
        )

        # Generate BMM file (placeholder)
        self.bmm_task = task.BmmGenerate(
            context=context,
            outputs=[bmm_resource],
        )

        # Convert Netlist
        self.net_task = task.NetlistConvert(
            context=context,
            inputs=[env_resource, ngc_resource, bmm_resource],
            outputs=[ngd_resource],
        )
        self.edif_task = task.EdifConvert(
            context=context,
            inputs=[env_resource, ngc_resource],
            outputs=[edif_resource],
        )

        # Physical Mapping
        self.map_task = task.Map(
            context=context,
            device=self.device,
            inputs=[env_resource, ngd_resource],
            outputs=[map_resource, pcf_resource],
        )

        # Place and Route
        self.par_task = task.Par(
            context=context,
            inputs=[env_resource, map_resource],
            outputs=[par_resource],
        )

        # Run Timing Analysis
        self.trce_task = task.Trce(
            context=context,
            inputs=[env_resource, par_resource, pcf_resource],
            outputs=[twr_resource],
        )

        # Run BITGEN
        self.bitgen_task = task.Bitgen(
            context=context,
            inputs=[env_resource, par_resource],
            outputs=[bit_resource],
        )

        # Add outputs to pending queue
        context.add_pending(ngc_resource)
        context.add_pending(edif_resource)
        context.add_pending(ngd_resource)
        context.add_pending(map_resource)
        context.add_pending(par_resource)
        context.add_pending(twr_resource)
        context.add_pending(bit_resource)
