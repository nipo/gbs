"""Vivado build tasks

Task implementations for Vivado FPGA synthesis and implementation flow.
Automatically selects between non-project mode (fast, for HDL-only designs)
and project mode (required for block designs and external IPs).
"""

from __future__ import annotations
import random
from pathlib import Path
from collections import defaultdict

from ...build.context import BuildContext
from ...build.task import Task, Resource
from ...build import tcl
from ...report_aggregator import TextReport, aggregate_text
from .project import ProjectCommand
from .vivado_tcl import Session

class NonProjectBuild(ProjectCommand):
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
        self.error_check("create the project")

        await self.update_progress(0.02, "User TCL")

        # Source init TCL files (both modes)
        for resource in inputs_by_type.get('vivado-init-tcl', []):
            self.debug(f"Sourcing init TCL: {resource.path}")
            await self.command_run(tcl.Command([
                "source", tcl.String(str(resource.path))
            ]))
        self.error_check("run the user init scripts")

        # Set up IP repository paths (both modes, no-op when empty)
        await self.ip_repos_setup(self.ip_repo_paths_collect(output_dir))
        self.error_check("set up the IP repositories")

        await self.update_progress(0.05, "Sources")

        # Block design handling (project mode only)
        if needs_project_mode:
            await self._add_block_designs(inputs_by_type, output_dir)
            self.error_check("add the block designs")

        # Add HDL and constraint sources (shared)
        await self.sources_add(self.inputs, 0.10, 0.05)

        # Set top module (shared)
        await self.top_set(topcell, top_lib)
        self.error_check("add the design sources")

        await self.update_progress(0.2, "IPs")

        # Build
        if needs_project_mode:
            await self._project_mode_build(topcell, userid, output_dir)
        else:
            await self._non_project_mode_build(topcell, userid)

        # Generate reports (shared — design is open in both modes at this point)
        await self._generate_reports()
        self.error_check("write the reports")

        self.info("Build complete")

    # ── Shared helpers ────────────────────────────────────────────

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
            await self.command_run(tcl.Command([
                "report_utilization", "-file", str(rsrc.path), "-append", "-hierarchical"
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
        await self.project_configure()

    async def _non_project_mode_build(self, topcell, userid):
        """Run synthesis+implementation in non-project mode"""
        # Generate IP targets
        await self.command_run(tcl.Command([
            "foreach", tcl.String("ip"),
            tcl.Expansion(["get_ips"]),
            tcl.String("generate_target {synthesis implementation} $ip; synth_ip $ip")
        ]))
        self.error_check("generate the IP cores")

        await self.update_progress(0.3, "Synth")

        # Synthesis
        self.info("Running synthesis")
        await self.command_run(tcl.Command([
            "synth_design", "-top", topcell, "-part", self.part, "-assert"
        ]))
        self.error_check("synthesize the design")

        await self.update_progress(0.4, "Opt")

        # Optimization
        self.info("Running optimization")
        await self.command_run(tcl.Command(["opt_design"]))
        self.error_check("optimize the design")

        await self.update_progress(0.5, "Place")

        # Place
        self.info("Running placement")
        await self.command_run(tcl.Command(["place_design"]))
        self.error_check("place the design")

        await self.update_progress(0.6, "Route")

        # Route
        self.info("Running routing")
        await self.command_run(tcl.Command(["route_design"]))
        self.error_check("route the design")

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
        self.error_check("write the bitstream")

    # ── Project mode (for BD/XCI/IP designs) ──────────────────────

    async def _project_mode_init(self, output_dir):
        """Initialize on-disk project for project mode"""
        await self.update_progress(0.01, "Init")

        self.info(f"Creating Vivado project for part {self.part}")
        await self.command_run(tcl.Command([
            "create_project", "synth", "project",
            "-part", self.part, "-force",
        ]))
        await self.project_configure()

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
        self.error_check("synthesize the design")
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
        self.error_check("implement the design")
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
        self.error_check("collect the bitstream")

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
        from ...report_aggregator import HtmlFragment
        from ...timing_summary import TIMING_SUMMARY_FILE_TYPE, TimingSummaryHtml

        reports = []
        for rsrc in self.inputs:
            if rsrc.file_type == TIMING_SUMMARY_FILE_TYPE:
                reports.append(HtmlFragment(
                    title="Timing Summary",
                    html=TimingSummaryHtml.fragment(rsrc.path),
                ))
            else:
                reports.append(TextReport.from_file(rsrc.path))

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(aggregate_text(reports, title="Vivado PnR Report"))
        self.info(f"Aggregated {len(reports)} PnR reports to {output.path}")
