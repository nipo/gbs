"""Vivado build tasks

Task implementations for Vivado FPGA synthesis and implementation flow.
Automatically selects between non-project mode (fast, for HDL-only designs)
and project mode (required for block designs and external IPs).
"""

from __future__ import annotations
import random
import shutil
import zipfile
from pathlib import Path
from collections import defaultdict

from ...build.context import BuildContext
from ...build.task import Task, Resource
from ...build import tcl
from ...report_aggregator import TextReport, aggregate_text
from .vivado_tcl import Session, VivadoCommand


class NonProjectBuild(VivadoCommand):
    """Run complete Vivado build flow.

    Automatically selects between:
    - Non-project mode (fast): for HDL-only designs without block designs
      or external IPs. Uses synth_design/opt_design/place_design/route_design.
    - Project mode: for designs with block designs (.bd), external IPs (.xci),
      or packaged IPs (vivado-ip-zip). Uses launch_runs/wait_on_run.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        session: Session,
        part: str,
        inputs: list = [],
        outputs: list = [],
    ):
        super().__init__(
            dispatcher=dispatcher,
            name="vivado_build",
            session=session,
            inputs=inputs,
            outputs=outputs,
            description="Vivado build"
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
        output_dir = self.dispatcher.context.output_path.resolve()
        userid = f"{random.randint(0, 0xFFFFFFFF):#010x}"

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

        # Detect if project mode is needed
        needs_project_mode = bool(
            inputs_by_type.get('vivado-block-design')
            or inputs_by_type.get('xilinx-xci')
            or inputs_by_type.get('vivado-ip-zip')
        )

        if needs_project_mode:
            self.info(f"Using project mode (design has BD/XCI/IP)")
            await self._project_mode_init(output_dir)
        else:
            self.info(f"Using non-project mode (HDL only)")
            await self._non_project_mode_init()

        await self.update_progress(0.02, "User TCL")

        # Source init TCL files (both modes)
        for resource in inputs_by_type.get('vivado-init-tcl', []):
            self.debug(f"Sourcing init TCL: {resource.path}")
            await self.command_run(tcl.Command([
                "source", tcl.String(str(resource.path))
            ]))

        # Set up IP repository paths (both modes, no-op when empty)
        await self._setup_ip_repos(inputs_by_type, output_dir)

        await self.update_progress(0.05, "Sources")

        # Block design handling (project mode only)
        if needs_project_mode:
            await self._add_block_designs(inputs_by_type, output_dir)

        # Add HDL and constraint sources (shared)
        await self._add_sources(inputs_by_type, inputs_by_library, seen_libraries)

        # Set top module (shared)
        self.debug(f"Setting top: {topcell} (lib={top_lib})")
        await self.command_run(tcl.Command([
            "set_property", "top_lib", top_lib, tcl.BareWord("$source_fileset_obj")
        ]))
        await self.command_run(tcl.Command([
            "set_property", "top", topcell, tcl.BareWord("$source_fileset_obj")
        ]))

        await self.update_progress(0.2, "IPs")

        # Build
        if needs_project_mode:
            await self._project_mode_build(topcell, userid, output_dir)
        else:
            await self._non_project_mode_build(topcell, userid)

        # Generate reports (shared — design is open in both modes at this point)
        await self._generate_reports()

        self.info("Build complete")

    # ── Shared helpers ────────────────────────────────────────────

    async def _setup_ip_repos(self, inputs_by_type, output_dir):
        """Set up IP repository paths for packaged IPs and bus definitions"""
        ip_repo_paths = []

        for resource in inputs_by_type.get('vivado-ip-zip', []):
            ip_unzip_dir = output_dir / "ip_repo" / resource.path.stem
            ip_unzip_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(resource.path, 'r') as zf:
                zf.extractall(ip_unzip_dir)
            ip_repo_paths.append(str(ip_unzip_dir))
            self.info(f"Extracted IP: {resource.path.name} -> {ip_unzip_dir}")

        bus_defs = inputs_by_type.get('vivado-bus-definition', [])
        if bus_defs:
            bus_repo_dir = output_dir / "bus_repo"
            bus_repo_dir.mkdir(parents=True, exist_ok=True)
            for bus_rsrc in bus_defs:
                shutil.copy2(bus_rsrc.path, bus_repo_dir / bus_rsrc.path.name)
            ip_repo_paths.append(str(bus_repo_dir))

        if ip_repo_paths:
            await self.command_run(tcl.Command([
                "set_property", "ip_repo_paths",
                tcl.Expansion([
                    "concat",
                    tcl.Expansion(["get_property", "ip_repo_paths",
                                   tcl.Expansion(["current_project"])]),
                    tcl.Expansion(["list"] + [tcl.String(p) for p in ip_repo_paths]),
                ]),
                tcl.Expansion(["current_project"]),
            ]))
            await self.command_run(tcl.Command(["update_ip_catalog", "-rebuild"]))

    async def _add_sources(self, inputs_by_type, inputs_by_library, seen_libraries):
        """Add HDL and constraint source files to the project"""
        for i, library in enumerate(seen_libraries):
            lib_files = inputs_by_library[library]

            await self.update_progress(
                0.10 + .05 * (i / max(len(seen_libraries), 1)),
                f"HDL {library}")

            # VHDL files
            for resource in lib_files.get('vhdl', []):
                vhdl_type = self._get_vhdl_file_type(resource)
                self.debug(f"Adding VHDL: {resource.path} (lib={library}, type={vhdl_type})")
                await self.command_run(tcl.Command([
                    "set", "f",
                    tcl.Expansion([
                        "add_files", "-norecurse", "-fileset",
                        tcl.BareWord("$source_fileset_obj"),
                        tcl.Expansion(["file", "normalize",
                                       tcl.String(str(resource.path))]),
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
                        "add_files", "-norecurse", "-fileset",
                        tcl.BareWord("$source_fileset_obj"),
                        tcl.Expansion(["file", "normalize",
                                       tcl.String(str(resource.path))]),
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
                        "add_files", "-norecurse", "-fileset",
                        tcl.BareWord("$constraints_fileset_obj"),
                        tcl.Expansion(["file", "normalize",
                                       tcl.String(str(resource.path))]),
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
                        "add_files", "-norecurse", "-fileset",
                        tcl.BareWord("$constraints_fileset_obj"),
                        tcl.Expansion(["file", "normalize",
                                       tcl.String(str(resource.path))]),
                    ])
                ]))
                await self.command_run(tcl.Command([
                    "set_property", "-dict",
                    tcl.String("file_type {TCL} used_in {synthesis implementation}"),
                    tcl.BareWord("$f")
                ]))

    async def _generate_reports(self):
        """Generate reports from the routed design (shared by both modes)"""
        await self.update_progress(0.7, "Reports")

        self.info("Generating reports")
        for rsrc in self.outputs_of_type("vivado-routing-report"):
            await self.command_run(tcl.Command([
                "report_route_status", "-file", str(rsrc.path)
            ]))

        for rsrc in self.outputs_of_type("vivado-timing-report"):
            await self.command_run(tcl.Command([
                "report_timing_summary", "-file", str(rsrc.path)
            ]))

        await self.update_progress(0.8, "Reports")

        for rsrc in self.outputs_of_type("vivado-power-report"):
            await self.command_run(tcl.Command([
                "report_power", "-file", str(rsrc.path)
            ]))

        for rsrc in self.outputs_of_type("vivado-usage-report"):
            await self.command_run(tcl.Command([
                "report_utilization", "-file", str(rsrc.path)
            ]))

        for rsrc in self.outputs_of_type("vivado-netlist-edif"):
            await self.command_run(tcl.Command([
                "write_edif", "-force", str(rsrc.path)
            ]))

        for rsrc in self.outputs_of_type("vivado-drc-report"):
            await self.command_run(tcl.Command([
                "report_drc", "-file", str(rsrc.path)
            ]))

    # ── Non-project mode (fast, HDL only) ─────────────────────────

    async def _non_project_mode_init(self):
        """Initialize in-memory project for non-project mode"""
        await self.update_progress(0.01, "Init")

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

    async def _non_project_mode_build(self, topcell, userid):
        """Run synthesis+implementation in non-project mode"""
        # Generate IP targets
        await self.command_run(tcl.Command([
            "foreach", tcl.String("ip"),
            tcl.Expansion(["get_ips"]),
            tcl.String("generate_target {synthesis implementation} $ip; synth_ip $ip")
        ]))

        await self.update_progress(0.3, "Synth")

        # Synthesis
        self.info("Running synthesis")
        await self.command_run(tcl.Command([
            "synth_design", "-top", topcell, "-part", self.part, "-assert"
        ]))

        await self.update_progress(0.4, "Opt")

        # Optimization
        self.info("Running optimization")
        await self.command_run(tcl.Command(["opt_design"]))

        await self.update_progress(0.5, "Place")

        # Place
        self.info("Running placement")
        await self.command_run(tcl.Command(["place_design"]))

        await self.update_progress(0.6, "Route")

        # Route
        self.info("Running routing")
        await self.command_run(tcl.Command(["route_design"]))

        # Set USERID
        await self.command_run(tcl.Command([
            "set_property", "BITSTREAM.CONFIG.USERID", userid,
            tcl.Expansion(["current_design"])
        ]))
        await self.command_run(tcl.Command([
            "set_param", "drc.maxLimitREQP1839and1840", "0"
        ]))

        # Generate bitstream
        await self.update_progress(0.9, "Bitstream")
        self.info("Generating bitstream")
        for rsrc in self.outputs_of_type("vivado-bitstream"):
            await self.command_run(tcl.Command([
                "write_bitstream", "-force", str(rsrc.path)
            ]))

    # ── Project mode (for BD/XCI/IP designs) ──────────────────────

    async def _project_mode_init(self, output_dir):
        """Initialize on-disk project for project mode"""
        await self.update_progress(0.01, "Init")

        self.info(f"Creating Vivado project for part {self.part}")
        await self.command_run(tcl.Command([
            "create_project", "synth", "project",
            "-part", self.part, "-force",
        ]))
        await self.command_run(tcl.Command([
            "set", tcl.BareWord("source_fileset_obj"),
            tcl.Expansion(["get_filesets", "sources_1"])
        ]))
        await self.command_run(tcl.Command([
            "set", tcl.BareWord("constraints_fileset_obj"),
            tcl.Expansion(["get_filesets", "constrs_1"])
        ]))

    async def _add_block_designs(self, inputs_by_type, output_dir):
        """Add block designs — copy to build dir, generate targets"""
        for resource in inputs_by_type.get('vivado-block-design', []):
            self.info(f"Adding block design: {resource.path}")
            bd_build_dir = output_dir / "bd-build"

            await self.command_run(tcl.Command([
                "set", tcl.BareWord("_bd_orig"),
                tcl.Expansion(["file", "normalize",
                               tcl.String(str(resource.path.resolve()))]),
            ]))
            await self.command_run(tcl.Command([
                "set", tcl.BareWord("_bd_dir"),
                tcl.String(str(bd_build_dir)),
            ]))
            await self.command_run(tcl.Command([
                "file", "mkdir", tcl.BareWord("$_bd_dir"),
            ]))
            await self.command_run(tcl.Command([
                "set", tcl.BareWord("_bd_copy"),
                tcl.Expansion(["file", "join", tcl.BareWord("$_bd_dir"),
                               tcl.Expansion(["file", "tail",
                                              tcl.BareWord("$_bd_orig")])]),
            ]))
            await self.command_run(tcl.Command([
                "file", "copy", "-force",
                tcl.BareWord("$_bd_orig"), tcl.BareWord("$_bd_copy"),
            ]))
            await self.command_run(tcl.Command([
                "set", tcl.BareWord("_bd_file"),
                tcl.Expansion(["add_files", tcl.BareWord("$_bd_copy")]),
            ]))
            await self.command_run(tcl.Command([
                "generate_target", "all", tcl.BareWord("$_bd_file"),
            ]))
            await self.command_run(tcl.Command([
                "export_ip_user_files", "-of_objects",
                tcl.BareWord("$_bd_file"),
                "-no_script", "-sync", "-force", "-quiet",
            ]))
            await self.command_run(tcl.Command([
                "create_ip_run", tcl.BareWord("$_bd_file"),
            ]))

    async def _project_mode_build(self, topcell, userid, output_dir):
        """Run synthesis+implementation via managed runs"""
        # Generate IP targets — only top-level (not children of BDs)
        await self.command_run(tcl.Command([
            "foreach", tcl.String("xci"),
            tcl.Expansion(["get_files", "-of_objects",
                           tcl.BareWord("$source_fileset_obj"), "*.xci"]),
            tcl.String(
                'if {[get_property parent_composite_file $xci] eq {}} {'
                '    generate_target "synthesis implementation" $xci'
                '}'
            ),
        ]))

        await self.update_progress(0.3, "Synth")

        # Synthesis via managed run
        self.info("Running synthesis")
        await self.command_run(tcl.Command([
            "launch_runs", "synth_1", "-jobs", "4",
        ]))
        await self.command_run(tcl.Command([
            "wait_on_run", "synth_1",
        ]))
        await self.command_run(tcl.Command([
            "if",
            tcl.String('[get_property PROGRESS [get_runs synth_1]] != "100%"'),
            tcl.String("error {Synthesis failed}"),
        ]))

        await self.update_progress(0.5, "Impl")

        # Implementation via managed run (through to write_bitstream)
        self.info("Running implementation")
        await self.command_run(tcl.Command([
            "launch_runs", "impl_1", "-jobs", "4",
            "-to_step", "write_bitstream",
        ]))
        await self.command_run(tcl.Command([
            "wait_on_run", "impl_1",
        ]))
        await self.command_run(tcl.Command([
            "if",
            tcl.String('[get_property PROGRESS [get_runs impl_1]] != "100%"'),
            tcl.String("error {Implementation failed}"),
        ]))

        # Extract bitstream from managed run output
        for rsrc in self.outputs_of_type("vivado-bitstream"):
            self.info(f"Copying bitstream to: {rsrc.path}")
            await self.command_run(tcl.Command([
                "file", "copy", "-force",
                f"project/synth.runs/impl_1/{topcell}.bit",
                str(rsrc.path),
            ]))

        # Open implementation run for report generation
        await self.command_run(tcl.Command([
            "open_run", "impl_1",
        ]))

        # Set USERID on the implementation
        await self.command_run(tcl.Command([
            "set_property", "BITSTREAM.CONFIG.USERID", userid,
            tcl.Expansion(["current_design"])
        ]))


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
            reports.append(TextReport.from_file(rsrc.path))

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
            reports.append(TextReport.from_file(rsrc.path))

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(aggregate_text(reports, title="Vivado PnR Report"))
        self.info(f"Aggregated {len(reports)} PnR reports to {output.path}")
