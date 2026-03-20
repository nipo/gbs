"""Vivado build tasks

Task implementations for Vivado FPGA synthesis and implementation flow
in non-project (in-memory) mode.
"""

from __future__ import annotations
import random
from pathlib import Path
from collections import defaultdict

from ...build.context import BuildContext
from ...build.task import Task, Resource
from ...build import tcl
from ...report_aggregator import TextReport, aggregate_text
from .vivado_tcl import Session, VivadoCommand


class NonProjectBuild(VivadoCommand):
    """Run complete Vivado non-project build flow

    Performs synthesis, implementation, and bitstream generation
    in a single in-memory project without creating project files on disk.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        session: Session,
        part: str,
        inputs: list = [],
        outputs: list = [],
    ):
        """Initialize non-project build task

        Args:
            dispatcher: Parent dispatcher
            session: Vivado TCL session
            part: Target part number (e.g., "xc7a35tcsg324-1")
            outputs: Dict mapping output type to Resource
        """
        super().__init__(
            dispatcher=dispatcher,
            name="vivado_build",
            session=session,
            inputs=inputs,
            outputs=outputs,
            description="Vivado non-project build"
        )
        self.part = part

    def _get_vhdl_file_type(self, resource) -> str:
        """Get Vivado file type for VHDL file based on version"""
        variant = resource.file_type_version or ''
        if variant == '2008':
            return "VHDL 2008"
        return "VHDL"

    async def work(self) -> None:
        """Run complete Vivado build flow"""
        topcell = self.dispatcher.context.get_topcell()
        top_lib = self.dispatcher.context.get_topcell_library() or "work"

        # Group inputs by type and library
        inputs_by_type = defaultdict(list)
        inputs_by_library = defaultdict(lambda: defaultdict(list))

        for resource in self.inputs:
            file_type = resource.file_type
            library = resource.library or 'work'
            inputs_by_type[file_type].append(resource)
            inputs_by_library[library][file_type].append(resource)

        # Get library order from input order (preserving first occurrence)
        seen_libraries = []
        for resource in self.inputs:
            lib = resource.library or 'work'
            if lib not in seen_libraries:
                seen_libraries.append(lib)

        await self.update_progress(0.01, "Init")

        # Create in-memory project
        self.info(f"Creating in-memory project for part {self.part}")
        await self.command_run(tcl.Command([
            "create_project", "-in_memory", "-part", self.part
        ]))
        await self.command_run(tcl.Command([
            "set_property", "source_mgmt_mode", "DisplayOnly",
            tcl.Expansion(["current_project"])
        ]))
        await self.command_run(tcl.Command([
            "set", tcl.BareWord("source_fileset_obj"),
            tcl.Expansion(["get_filesets", "sources_1"])
        ]))
        await self.command_run(tcl.Command([
            "set", tcl.BareWord("constraints_fileset_obj"),
            tcl.Expansion(["get_filesets", "constrs_1"])
        ]))

        await self.update_progress(0.02, "User TCL")

        # Source init TCL files
        for resource in inputs_by_type.get('vivado-init-tcl', []):
            self.debug(f"Sourcing init TCL: {resource.path}")
            await self.command_run(tcl.Command([
                "source", tcl.String(str(resource.path))
            ]))

        await self.update_progress(0.05, "Block designs")
            
        # Add block designs
        for resource in inputs_by_type.get('vivado-block-design', []):
            self.debug(f"Adding block design: {resource.path}")
            await self.command_run(tcl.Command([
                "set", "f",
                tcl.Expansion(["add_files", tcl.String(str(resource.path))])
            ]))
            await self.command_run(tcl.Command([
                "reset_target", "Synthesis", tcl.BareWord("$f")
            ]))
            await self.command_run(tcl.Command([
                "open_bd_design", tcl.BareWord("$f")
            ]))

        # Add files by library in dependency order
        for i, library in enumerate(seen_libraries):
            lib_files = inputs_by_library[library]

            await self.update_progress(0.10 + .05 * (i / len(seen_libraries)), f"HDL {library}")

            # VHDL files
            for resource in lib_files.get('vhdl', []):
                vhdl_type = self._get_vhdl_file_type(resource)
                self.debug(f"Adding VHDL: {resource.path} (lib={library}, type={vhdl_type})")
                await self.command_run(tcl.Command([
                    "set", "f",
                    tcl.Expansion([
                        "add_files", "-norecurse", "-fileset", tcl.BareWord("$source_fileset_obj"),
                        tcl.String(str(resource.path))
                    ])
                ]))
                await self.command_run(tcl.Command([
                    "set_property", "-dict",
                    tcl.String(f"file_type {{{vhdl_type}}} library {{{library}}}"),
                    tcl.BareWord("$f")
                ]))

            # Verilog files
            for resource in lib_files.get('verilog', []):
                self.debug(f"Adding Verilog: {resource.path} (lib={library})")
                await self.command_run(tcl.Command([
                    "set", "f",
                    tcl.Expansion([
                        "add_files", "-norecurse", "-fileset", tcl.BareWord("$source_fileset_obj"),
                        tcl.String(str(resource.path))
                    ])
                ]))
                await self.command_run(tcl.Command([
                    "set_property", "-dict",
                    tcl.String(f"file_type {{Verilog}} library {{{library}}}"),
                    tcl.BareWord("$f")
                ]))

            # XCI (IP) files
            for resource in lib_files.get('xilinx-xci', []):
                self.debug(f"Adding XCI: {resource.path} (lib={library})")
                await self.command_run(tcl.Command([
                    "set", "f",
                    tcl.Expansion(["read_ip", tcl.String(str(resource.path))])
                ]))
                await self.command_run(tcl.Command([
                    "set_property", "-dict",
                    tcl.String(f"library {{{library}}} used_in {{synthesis implementation}}"),
                    tcl.BareWord("$f")
                ]))

            # XDC constraint files
            for resource in lib_files.get('xilinx-xdc', []):
                self.debug(f"Adding XDC: {resource.path}")
                await self.command_run(tcl.Command([
                    "set", "f",
                    tcl.Expansion([
                        "add_files", "-norecurse", "-fileset", tcl.BareWord("$constraints_fileset_obj"),
                        tcl.String(str(resource.path))
                    ])
                ]))
                await self.command_run(tcl.Command([
                    "set_property", "-dict",
                    tcl.String("file_type {XDC} used_in {synthesis implementation}"),
                    tcl.BareWord("$f")
                ]))

            # TCL constraint files
            for resource in lib_files.get('xilinx-constraints-tcl', []):
                self.debug(f"Adding constraints TCL: {resource.path}")
                await self.command_run(tcl.Command([
                    "set", "f",
                    tcl.Expansion([
                        "add_files", "-norecurse", "-fileset", tcl.BareWord("$constraints_fileset_obj"),
                        tcl.String(str(resource.path))
                    ])
                ]))
                await self.command_run(tcl.Command([
                    "set_property", "-dict",
                    tcl.String("file_type {TCL} used_in {synthesis implementation}"),
                    tcl.BareWord("$f")
                ]))

        # Set design properties
        userid = f"{random.randint(0, 0xFFFFFFFF):#010x}"
        self.debug(f"Setting USERID to {userid}")
        await self.command_run(tcl.Command([
            "set_property", "BITSTREAM.CONFIG.USERID", userid,
            tcl.Expansion(["current_design"])
        ]))
        await self.command_run(tcl.Command([
            "set_param", "drc.maxLimitREQP1839and1840", "0"
        ]))
        await self.command_run(tcl.Command([
            "set_param", "synth.elaboration.rodinMoreOptions",
            tcl.String("rt::set_parameter ignoreVhdlAssertStmts false")
        ]))

        # Set top module
        self.debug(f"Setting top: {topcell} (lib={top_lib})")
        await self.command_run(tcl.Command([
            "set_property", "top_lib", top_lib, tcl.BareWord("$source_fileset_obj")
        ]))
        await self.command_run(tcl.Command([
            "set_property", "top", topcell, tcl.BareWord("$source_fileset_obj")
        ]))

        await self.update_progress(0.2, f"IPs")

        # Generate IP targets
        await self.command_run(tcl.Command([
            "foreach", tcl.String("xci"),
            tcl.Expansion(["get_files", "-of_objects", tcl.BareWord("$source_fileset_obj"), "*.xci"]),
            tcl.String("generate_target {synthesis implementation} $xci")
        ]))
        await self.command_run(tcl.Command([
            "foreach", tcl.String("ip"),
            tcl.Expansion(["get_ips"]),
            tcl.String("generate_target {synthesis implementation} $ip; synth_ip $ip")
        ]))

        await self.update_progress(0.3, f"Synth")

        # Synthesis
        self.info("Running synthesis")
        await self.command_run(tcl.Command([
            "synth_design", "-top", topcell, "-part", self.part, "-assert"
        ]))

        await self.update_progress(0.4, f"Opt")
        
        # Optimization
        self.info("Running optimization")
        await self.command_run(tcl.Command(["opt_design"]))

        await self.update_progress(0.5, f"Place")
        
        # Place
        self.info("Running placement")
        await self.command_run(tcl.Command(["place_design"]))

        await self.update_progress(0.6, f"Route")

        # Route
        self.info("Running routing")
        await self.command_run(tcl.Command(["route_design"]))

        await self.update_progress(0.7, f"Reports")
        
        # Generate reports
        self.info("Generating reports")
        for rsrc in self.outputs_of_type("vivado-routing-report"):
            await self.command_run(tcl.Command([
                "report_route_status", "-file",
                str(rsrc.path)
            ]))

        for rsrc in self.outputs_of_type("vivado-timing-report"):
            await self.command_run(tcl.Command([
                "report_timing_summary", "-file",
                str(rsrc.path)
            ]))

        await self.update_progress(0.8, f"Reports")
            
        for rsrc in self.outputs_of_type("vivado-power-report"):
            await self.command_run(tcl.Command([
                "report_power", "-file",
                str(rsrc.path)
            ]))

        for rsrc in self.outputs_of_type("vivado-usage-report"):
            await self.command_run(tcl.Command([
                "report_utilization", "-file",
                str(rsrc.path)
            ]))

        for rsrc in self.outputs_of_type("vivado-netlist-edif"):
            await self.command_run(tcl.Command([
                "write_edif", "-force",
                str(rsrc.path)
            ]))
            
        for rsrc in self.outputs_of_type("vivado-drc-report"):
            await self.command_run(tcl.Command([
                "report_drc", "-file",
                str(rsrc.path)
            ]))

        await self.update_progress(0.9, f"Bitstream")
            
        # Generate bitstream
        self.info("Generating bitstream")
        for rsrc in self.outputs_of_type("vivado-bitstream"):
            await self.command_run(tcl.Command([
                "write_bitstream", "-force",
                str(rsrc.path)
            ]))

            self.info(f"Bitstream saved to: {rsrc.path}")


class AggregateSynthesisReport(Task):
    """Aggregate Vivado synthesis text reports into a single HTML file."""

    REPORT_TYPES = ["vivado-usage-report"]

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list[Resource],
        outputs: list[Resource],
    ):
        super().__init__(dispatcher,
            name="vivado_synthesis_report",
            inputs=inputs,
            outputs=outputs,
            description="Aggregate Vivado synthesis reports",
        )

    async def work(self) -> None:
        reports = []
        for rsrc in self.inputs:
            if rsrc.path.exists():
                reports.append(TextReport.from_file(rsrc.path))
            else:
                self.warning(f"Report not found: {rsrc.path}")

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(aggregate_text(reports, title="Vivado Synthesis Report"))
        self.info(f"Aggregated {len(reports)} synthesis reports to {output.path}")


class AggregatePnrReport(Task):
    """Aggregate Vivado PnR text reports into a single HTML file."""

    REPORT_TYPES = [
        "vivado-routing-report",
        "vivado-timing-report",
        "vivado-power-report",
        "vivado-drc-report",
    ]

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list[Resource],
        outputs: list[Resource],
    ):
        super().__init__(dispatcher,
            name="vivado_pnr_report",
            inputs=inputs,
            outputs=outputs,
            description="Aggregate Vivado PnR reports",
        )

    async def work(self) -> None:
        reports = []
        for rsrc in self.inputs:
            if rsrc.path.exists():
                reports.append(TextReport.from_file(rsrc.path))
            else:
                self.warning(f"Report not found: {rsrc.path}")

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(aggregate_text(reports, title="Vivado PnR Report"))
        self.info(f"Aggregated {len(reports)} PnR reports to {output.path}")
