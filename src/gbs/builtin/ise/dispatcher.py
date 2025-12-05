"""Xilinx ISE Dispatcher implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext, BuildFileSet, BuildResource
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
        output_dir: Path | str = "build",
        output_base_name: str | None = None,
        target: dict[str, str] = {},
    ):
        super().__init__("ise", priority=600)
        self.output_dir = Path(output_dir)
        self.output_base_name = output_base_name
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
        context: BuildContext,
        fileset: BuildFileSet
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
            await self._task_graph_create(context, fileset, output_base_name)

        await self._sources_hookup(context, fileset)

    async def _sources_hookup(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
    ) -> None:
        # Add HDL input resources
        for source in list(fileset.filter(file_type=["vhdl", "verilog"])):
            for d in fileset.remove(source.path):
                self.xst_task.dependency_add(d)
            
            resource = context.get_resource(source.path, metadata = {
                'file_type': source.file_type,
                'library': source.library,
            })

            self.xst_task.inputs.append(resource)

        # Add UCF input resources
        for source in fileset.filter(file_type="xilinx-ucf"):
            resource = context.get_resource(source.path, metadata = {
                'file_type': "xilinx-ucf",
            })
            self.net_task.inputs.append(resource)

    async def _task_graph_create(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
        output_base_name: str
    ) -> None:
        """Create all ISE build tasks

        Creates the full chain: XST -> NGDBUILD -> MAP -> PAR -> TRCE -> BITGEN
        """
        config = context.get_tool(self.tool)
        ise_path = Path(config["path"])

        env_resource = context.get_resource(ise_path / "ISE_DS" / "settings64.sh",
                                            dict(file_type = "ise-settings-sh"))

        # Define intermediate file paths
        ngc_path = self.output_dir / "syn" / f"project.ngc"
        edif_path = self.output_dir / "syn" / f"project.edif"
        bmm_path = self.output_dir / "func" / f"project.bmm"
        ngd_path = self.output_dir / "func" / f"project.ngd"
        map_path = self.output_dir / "map" / f"project.map.ncd"
        pcf_path = self.output_dir / "map" / f"project.map.pcf"
        par_path = self.output_dir / "par" / f"project.par.ncd"
        twr_path = self.output_dir / "par" / f"project.twr"
        bit_path = self.output_dir / f"project.bit"

        # Create resources for all files
        ngc_resource = context.get_resource(ngc_path, dict(file_type = "ise-netlist-xst"))
        edif_resource = context.get_resource(edif_path, dict(file_type = "ise-netlist"))
        bmm_resource = context.get_resource(bmm_path, dict(file_type = "ise-bmm"))
        ngd_resource = context.get_resource(ngd_path, dict(file_type = "ise-netlist-functional"))
        map_resource = context.get_resource(map_path, dict(file_type = "ise-netlist-partial"))
        pcf_resource = context.get_resource(pcf_path, dict(file_type = "ise-physical-constraints"))
        par_resource = context.get_resource(par_path, dict(file_type = "ise-netlist-full"))
        twr_resource = context.get_resource(twr_path, dict(file_type = "ise-timing-report"))
        bit_resource = context.get_resource(bit_path, dict(file_type = "ise-bitstream"))

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

        # Add outputs to fileset
        fileset.add(BuildResource(
            resource=ngc_resource,
            file_type="ise-netlist",
            library="work",
            is_source=False,
            generated_by=self.name
        ))

        fileset.add(BuildResource(
            resource=ngd_resource,
            file_type="ise-netlist-functional",
            library="work",
            is_source=False,
            generated_by=self.name
        ))

        fileset.add(BuildResource(
            resource=map_resource,
            file_type="ise-netlist-partial",
            library="work",
            is_source=False,
            generated_by=self.name
        ))

        fileset.add(BuildResource(
            resource=par_resource,
            file_type="ise-netlist-full",
            library="work",
            is_source=False,
            generated_by=self.name
        ))

        fileset.add(BuildResource(
            resource=twr_resource,
            file_type="ise-timing-report",
            library=None,
            is_source=False,
            generated_by=self.name
        ))

        fileset.add(BuildResource(
            resource=bit_resource,
            file_type="ise-bitstream",
            library=None,
            is_source=False,
            generated_by=self.name
        ))
