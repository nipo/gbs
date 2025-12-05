"""Generic output copy Dispatcher implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext, BuildFileSet, BuildResource
from . import task

class OutputCopyDispatcher(BaseDispatcher):
    """
    Output copy dispatcher
    """

    def __init__(self):
        super().__init__("copy", priority=600)

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Process to create build context outputs
        """

        # Get target from project config
        if not context.project:
            raise ValueError("No project configured")

        # Get output base name
        output_base_name = self.output_base_name or context.get_topcell()

        # Check if we have HDL sources
        has_hdl = bool(fileset.filter(file_type="vhdl") or fileset.filter(file_type="verilog"))

        if has_hdl and not self._tasks_created:
            self.logger.debug("Creating all ISE build tasks")
            await self._create_all_tasks(context, fileset, output_base_name)
            self._tasks_created = True

    async def _sources_hookup(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
    ) -> None:
        topcell = context.get_topcell()
        
        # Get HDL sources in dependency order
        vhdl_sources = list(fileset.filter(file_type="vhdl"))
        verilog_sources = list(fileset.filter(file_type="verilog"))

        # Get UCF constraint files
        ucf_sources = list(fileset.filter(file_type="xilinx-ucf"))

        # Create HDL input resources with metadata
        hdl_input_resources = []
        for source in vhdl_sources + verilog_sources:
            resource = context.get_resource(source.path, metadata = {
                'file_type': source.file_type,
                'library': source.library,
                'language': 'vhdl' if source in vhdl_sources else 'verilog',
            })
            hdl_input_resources.append(resource)

        # Create UCF input resources
        ucf_resources = []
        for source in ucf_sources:
            resource = context.get_resource(source.path, metadata = {
                'file_type': "xilinx-ucf",
            })
            ucf_resources.append(resource)

    async def _create_all_tasks(
        self,
        context: BuildContext,
        output_base_name: str
    ) -> None:
        """Create all ISE build tasks

        Creates the full chain: XST -> NGDBUILD -> MAP -> PAR -> TRCE -> BITGEN
        """
        # Define intermediate file paths
        src_path = self.output_dir / "syn" / f"{output_base_name}.prj"
        ngc_path = self.output_dir / "syn" / f"{output_base_name}.ngc"
        bmm_path = self.output_dir / "func" / f"{output_base_name}.bmm"
        ngd_path = self.output_dir / "func" / f"{output_base_name}.ngd"
        map_path = self.output_dir / "map" / f"{output_base_name}.map.ncd"
        pcf_path = self.output_dir / "map" / f"{output_base_name}.map.pcf"
        par_path = self.output_dir / "par" / f"{output_base_name}.par.ncd"
        twr_path = self.output_dir / "par" / f"{output_base_name}.twr"
        bit_path = self.output_dir / f"{output_base_name}.bit"

        # Create resources for all files
        src_resource = context.get_resource(src_path, dict(file_type = "ise-xst-sourcelist"))
        ngc_resource = context.get_resource(ngc_path, dict(file_type = "ise-netlist"))
        bmm_resource = context.get_resource(bmm_path, dict(file_type = "ise-bmm"))
        ngd_resource = context.get_resource(ngd_path, dict(file_type = "ise-netlist-functional"))
        map_resource = context.get_resource(map_path, dict(file_type = "ise-netlist-partial"))
        pcf_resource = context.get_resource(pcf_path, dict(file_type = "ise-physical-constraints"))
        par_resource = context.get_resource(par_path, dict(file_type = "ise-netlist-full"))
        twr_resource = context.get_resource(twr_path, dict(file_type = "ise-timing-report"))
        bit_resource = context.get_resource(bit_path, dict(file_type = "ise-bitstream"))

        # Task 1: Generate PRJ file (source list)
        self.src_task = task.XstSourceList(
            context=context,
            inputs=[],
            outputs=[src_resource],
        )

        # Task 2: Run XST synthesis
        self.xst_task = task.XstRun(
            context=context,
            device=self.device,
            topcell=topcell,
            inputs=[src_resource],
            outputs=[ngc_resource],
        )

        # Task 3: Generate BMM file (placeholder)
        self.bmm_task = task.BmmGenerate(
            context=context,
            inputs=[],
            outputs=[bmm_resource],
        )

        # Task 4: Convert Netlist
        self.net_task = task.NetlistConvert(
            context=context,
            inputs=[ngc_resource, bmm_resource],
            outputs=[ngd_resource],
        )

        # Task 5: Physical Mapping
        self.map_task = task.Map(
            context=context,
            device=self.device,
            inputs=[ngd_resource],
            outputs=[map_resource, pcf_resource],
        )

        # Task 6: Place and Route
        self.par_task = task.Par(
            context=context,
            inputs=[map_resource],
            outputs=[par_resource],
        )

        # Task 7: Run Timing Analysis
        self.trce_task = task.Trce(
            context=context,
            inputs=[par_resource, pcf_resource],
            outputs=[twr_resource],
        )

        # Task 8: Run BITGEN
        self.bitgen_task = task.Bitgen(
            context=context,
            inputs=[par_resource],
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
