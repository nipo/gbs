"""Altera Quartus Dispatcher implementation"""

from __future__ import annotations
from pathlib import Path

from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from ...utils import expand_path
from . import task


class QuartusDispatcher(BaseDispatcher):
    """Altera Quartus FPGA synthesis backend

    Workflow (all tasks created on first process() call with HDL sources):

    1. ProjectSetup:
       - Generates .qpf and .qsf project files
       - Lists all HDL sources, device, pin assignments, SDC constraints

    2. QuartusMap (Analysis & Synthesis):
       - Runs quartus_map
       - Produces synthesis report

    3. QuartusFit (Fitter / Place & Route):
       - Runs quartus_fit
       - Produces fitting report

    4. QuartusSta (Timing Analysis):
       - Runs quartus_sta
       - Produces timing report

    5. QuartusAsm (Assembler):
       - Runs quartus_asm
       - Produces .sof bitstream
    """

    def __init__(
            self,
            context: BuildContext,
            vhdl_std: str,
            tool: str,
            target: dict[str, str] = {},
    ):
        super().__init__(context, "quartus", tool_name=tool)
        self.project_name = "project"
        self.target = target
        self.device = target["part"]
        self.tool = tool
        self.vhdl_std = vhdl_std

        self._setup_task = None
        self._map_task = None
        self._fit_task = None
        self._sta_task = None

    def _get_quartus_bin(self) -> Path:
        """Get path to quartus bin directory"""
        config = self.context.get_tool(self.tool)
        quartus_path = expand_path(config["path"])
        return quartus_path / "quartus" / "bin"

    @property
    def is_pro(self) -> bool:
        """Detect Quartus Prime Pro edition (uses quartus_syn instead of quartus_map)

        Detected by checking the version string of quartus_sh, since both
        editions ship both executables (one as a stub that errors out).
        """
        if not hasattr(self, '_is_pro'):
            import subprocess
            try:
                result = subprocess.run(
                    [str(self._get_quartus_bin() / "quartus_sh"), "--version"],
                    capture_output=True, text=True, timeout=10,
                )
                self._is_pro = "Pro Edition" in result.stdout
            except (subprocess.TimeoutExpired, FileNotFoundError):
                self._is_pro = False
        return self._is_pro

    async def process(self) -> None:
        """Process HDL sources and constraints"""
        if not self.context.project:
            raise ValueError("No project configured")

        if not self._setup_task:
            await self._task_graph_create()

        await self._sources_hookup()

    async def _sources_hookup(self) -> None:
        """Attach pending sources to the tasks that consume them.

        - HDL sources → quartus_map (reads the actual content)
                       + project setup (lists paths in QSF, consume=False)
        - Pin assignments → project setup (appended verbatim into QSF)
        - SDC constraints → project setup (path listed in QSF, consume=False)
                           + quartus_sta (reads actual content, consume=False)
        """
        existing_map = {r.path for r in self._map_task.inputs}
        existing_setup = {r.path for r in self._setup_task.inputs}
        existing_sta = {r.path for r in self._sta_task.inputs}

        for resource in self.context.filter_pending(file_type=["vhdl", "verilog"]):
            if resource.path not in existing_map:
                self._map_task.add_input(resource)
            if resource.path not in existing_setup:
                self._setup_task.add_input(resource, consume=False)

        for resource in self.context.filter_pending(file_type=["quartus-pin-assignment"]):
            if resource.path not in existing_setup:
                self._setup_task.add_input(resource)

        for resource in self.context.filter_pending(file_type=["quartus-sdc"]):
            if resource.path not in existing_setup:
                self._setup_task.add_input(resource, consume=False)
            if resource.path not in existing_sta:
                self._sta_task.add_input(resource, consume=False)

    async def _task_graph_create(self) -> None:
        """Create all Quartus build tasks"""
        quartus_bin = self._get_quartus_bin()
        op = self.context.output_path
        output_files = op / "output_files"

        def intermediate(path, file_type, **kwargs):
            return self.context.get_resource(
                path, file_type=file_type,
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name, **kwargs)

        # Project files
        qpf_resource = intermediate(op / f"{self.project_name}.qpf", "quartus-qpf")
        qsf_resource = intermediate(op / f"{self.project_name}.qsf", "quartus-qsf")

        # Primary build artifacts and report side-products
        pn = self.project_name

        # Pro edition uses .syn.rpt instead of .map.rpt
        syn_rpt_name = f"{pn}.syn.rpt" if self.is_pro else f"{pn}.map.rpt"
        map_report = intermediate(output_files / syn_rpt_name, "quartus-map-report")
        map_summary = intermediate(output_files / f"{pn}.map.summary", "quartus-map-summary")
        fit_report = intermediate(output_files / f"{pn}.fit.rpt", "quartus-fit-report")
        fit_summary = intermediate(output_files / f"{pn}.fit.summary", "quartus-fit-summary")
        pin_report = intermediate(output_files / f"{pn}.pin", "quartus-pin-report")
        sta_report = intermediate(output_files / f"{pn}.sta.rpt", "quartus-sta-report")
        sta_summary = intermediate(output_files / f"{pn}.sta.summary", "quartus-sta-summary")
        flow_report = intermediate(output_files / f"{pn}.flow.rpt", "quartus-flow-report")
        sof_resource = intermediate(output_files / f"{pn}.sof", "quartus-sof")
        asm_report = intermediate(output_files / f"{pn}.asm.rpt", "quartus-asm-report")

        # Project setup
        self._setup_task = task.ProjectSetup(
            dispatcher=self,
            device=self.device,
            vhdl_std=self.vhdl_std,
            project_name=self.project_name,
            inputs=[],
            outputs=[qpf_resource, qsf_resource],
        )

        # Analysis & Synthesis
        # Pro edition renamed quartus_map to quartus_syn
        synthesis_executable = "quartus_syn" if self.is_pro else "quartus_map"
        self._map_task = task.QuartusMap(
            dispatcher=self,
            quartus_bin=quartus_bin,
            project_name=self.project_name,
            executable=synthesis_executable,
            inputs=[qsf_resource],
            outputs=[map_report, map_summary],
        )

        # Fitter
        self._fit_task = task.QuartusFit(
            dispatcher=self,
            quartus_bin=quartus_bin,
            project_name=self.project_name,
            inputs=[map_report],
            outputs=[fit_report, fit_summary, pin_report],
        )

        # Timing Analysis
        self._sta_task = task.QuartusSta(
            dispatcher=self,
            quartus_bin=quartus_bin,
            project_name=self.project_name,
            inputs=[fit_report],
            outputs=[sta_report, sta_summary],
        )

        # Assembler
        task.QuartusAsm(
            dispatcher=self,
            quartus_bin=quartus_bin,
            project_name=self.project_name,
            inputs=[fit_report],
            outputs=[sof_resource, asm_report, flow_report],
        )

        # Report aggregation on demand
        for dest in self.context.filter_pending(file_type="quartus-synthesis-report"):
            task.AggregateReport(
                dispatcher=self,
                name="quartus_synthesis_report",
                title="Quartus Synthesis Report",
                inputs=[map_report, map_summary],
                outputs=[dest],
            )

        for dest in self.context.filter_pending(file_type="quartus-pnr-report"):
            task.AggregateReport(
                dispatcher=self,
                name="quartus_pnr_report",
                title="Quartus PnR Report",
                inputs=[fit_report, fit_summary, pin_report, sta_report, sta_summary, flow_report],
                outputs=[dest],
            )
