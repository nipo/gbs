"""Altera Quartus Dispatcher implementation"""

from __future__ import annotations
from pathlib import Path

from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import Resource, ResourceTypology
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

    6. QuartusPfg (Programming file generator):
       - Runs quartus_pfg
       - Produces .jam STAPL
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
        self.vhdl_std = vhdl_std

        self._setup_task = None
        self._map_task = None
        self._fit_task = None
        self._sta_task = None
        self._qsys_qip = {}
        self._qsys_scripts = {}

    def _get_quartus_bin(self) -> Path:
        """Get path to quartus bin directory"""
        quartus_path = expand_path(self.get_tool_option("path"))
        return quartus_path / "quartus" / "bin"

    def _get_qsys_bin(self) -> Path:
        """Get path to qsys bin directory (sibling of quartus/bin under the same install)"""
        quartus_path = expand_path(self.get_tool_option("path"))
        return quartus_path / "qsys" / "bin"

    @property
    def is_pro(self) -> bool:
        """Detect Quartus Prime Pro edition (uses quartus_syn instead of quartus_map)

        Detected by checking the version string of quartus_sh, since both
        editions ship both executables (one as a stub that errors out).
        """
        if not hasattr(self, '_is_pro'):
            import subprocess
            try:
                from ...utils import resolve_tool_exe
                result = subprocess.run(
                    [str(resolve_tool_exe(self._get_quartus_bin() / "quartus_sh")), "--version"],
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
        - Qsys systems → qsys_generate (produces a .qip)
                       → project setup (.qip path listed in QSF, consume=False)
        - Qsys scripts → qsys_script (produces a .qsys)
                       → qsys_generate → project setup (as above)

        _map_task/_sta_task are None when only quartus-project was
        requested (no synthesis pipeline was built) — sources still get
        listed in the .qsf via _setup_task so the exported project has
        them ready, but there's nothing to feed them to for synthesis.
        """
        existing_map = {r.path for r in self._map_task.inputs} if self._map_task else set()
        existing_setup = {r.path for r in self._setup_task.inputs}
        existing_sta = {r.path for r in self._sta_task.inputs} if self._sta_task else set()

        for resource in self.context.filter_pending(file_type=["vhdl", "verilog"]):
            if self._map_task and resource.path not in existing_map:
                self._map_task.add_input(resource)
            if resource.path not in existing_setup:
                self._setup_task.add_input(resource, consume=False)

        for resource in self.context.filter_pending(file_type=["quartus-pin-assignment"]):
            if resource.path not in existing_setup:
                self._setup_task.add_input(resource)

        for resource in self.context.filter_pending(file_type=["quartus-sdc"]):
            if resource.path not in existing_setup:
                self._setup_task.add_input(resource, consume=False)
            if self._sta_task and resource.path not in existing_sta:
                self._sta_task.add_input(resource, consume=False)

        for resource in self.context.filter_pending(file_type=["quartus-qsys"]):
            if resource.path not in self._qsys_qip:
                self._qsys_qip[resource.path] = self._create_qsys_generate_task(resource)

        for resource in self.context.filter_pending(file_type=["quartus-qsys-script"]):
            if resource.path not in self._qsys_scripts:
                qsys_resource = self._create_qsys_script_task(resource)
                self._qsys_scripts[resource.path] = qsys_resource
                self._qsys_qip[qsys_resource.path] = self._create_qsys_generate_task(qsys_resource)

        for qip_resource in self._qsys_qip.values():
            if qip_resource.path not in existing_setup:
                self._setup_task.add_input(qip_resource, consume=False)

    def _create_qsys_script_task(self, tcl_resource: Resource) -> Resource:
        """Create a qsys_script task for a single .tcl file, returning its generated .qsys output

        Written under a dedicated scripts/<system_name>/ subfolder: separate
        from where _create_qsys_generate_task stages its own .qsys copy, so
        the two tasks never write into the same directory, and scoped per
        system (not just scripts/) because QsysScript.work() wipes this
        directory on every run to avoid colliding with qsys-script's
        auto-created companion project from a previous run — sharing it
        across systems would risk deleting a sibling's output mid-build.

        The whole ip/ tree next to the source .tcl (not just
        ip/<system_name>/) is tracked here too, since a script's
        add_component calls can reference any system's .ip files by
        relative path, not just its own.
        """
        system_name = tcl_resource.path.stem
        qsys_out_dir = self.context.output_path / "output_files" / "qsys" / "scripts" / system_name
        qsys_resource = self.context.get_resource(
            qsys_out_dir / f"{system_name}.qsys",
            file_type="quartus-qsys",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        script_task = task.QsysScript(
            dispatcher=self,
            qsys_bin=self._get_qsys_bin(),
            inputs=[tcl_resource],
            outputs=[qsys_resource],
        )
        self.attach_definition_dependencies(script_task)

        source_ip_dir = tcl_resource.path.parent / "ip"
        if source_ip_dir.is_dir():
            for ip_file in sorted(source_ip_dir.rglob("*.ip")):
                ip_resource = self.context.get_resource(
                    ip_file, file_type="quartus-qsys-ip", typology=ResourceTypology.SOURCE,
                )
                script_task.add_input(ip_resource, consume=False)

        return qsys_resource

    def _create_qsys_generate_task(self, qsys_resource: Resource) -> Resource:
        """Create a qsys_generate task for a single .qsys file, returning its .qip output

        qsys-generate has no output-location flag: it always writes
        <system_name>/<system_name>.qip as a sibling directory of
        whatever .qsys file it's given (verified against qsys-generate
        --help — no --output-directory or similar option exists). To
        keep that output under gbs-build regardless of where the source
        .qsys lives, QsysGenerate.work() stages a copy of it here, at
        output_files/qsys/<system_name>.qsys, before running the tool —
        so the .qip Resource can be declared at the build-scoped
        sibling location computed below.

        Any ip/<system_name>/*.ip Generic Component files next to the
        source .qsys are attached as non-consuming inputs too (not
        declared as project sources, so filter_pending never sees them —
        they have to be picked up here instead), so editing one marks
        this task stale the same way editing the .qsys itself would.
        """
        system_name = qsys_resource.path.stem
        qsys_out_dir = self.context.output_path / "output_files" / "qsys" / system_name
        qip_resource = self.context.get_resource(
            qsys_out_dir / f"{system_name}.qip",
            file_type="quartus-qip",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        qsys_task = task.QsysGenerate(
            dispatcher=self,
            qsys_bin=self._get_qsys_bin(),
            inputs=[qsys_resource],
            outputs=[qip_resource],
        )
        self.attach_definition_dependencies(qsys_task)

        source_ip_dir = qsys_resource.path.parent / "ip" / system_name
        if source_ip_dir.is_dir():
            for ip_file in sorted(source_ip_dir.glob("*.ip")):
                ip_resource = self.context.get_resource(
                    ip_file, file_type="quartus-qsys-ip", typology=ResourceTypology.SOURCE,
                )
                qsys_task.add_input(ip_resource, consume=False)

        return qip_resource

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

        # Project setup — always needed, it's what produces the .qpf/.qsf
        self._setup_task = task.ProjectSetup(
            dispatcher=self,
            device=self.device,
            vhdl_std=self.vhdl_std,
            project_name=self.project_name,
            inputs=[],
            outputs=[qpf_resource, qsf_resource],
        )
        self.attach_definition_dependencies(self._setup_task)

        # quartus-project: export the project as-is, independent of
        # whether synthesis also runs below
        for dest in self.context.filter_pending(file_type="quartus-project"):
            task.QuartusProjectExport(
                dispatcher=self,
                inputs=[qpf_resource, qsf_resource],
                outputs=[dest],
            )

        # If nothing that needs synthesis was requested, stop here.
        # Creating QuartusMap/Fit/Sta/Asm would make BuildContext._launch()
        # run them unconditionally regardless of whether anything depends
        # on their output (it launches every registered task), so the
        # only way to actually skip synthesis is to never construct these
        # tasks in the first place.
        needs_synthesis = any(
            self.context.filter_pending(file_type=t)
            for t in ("quartus-sof", "quartus-jam", "quartus-rbf", "quartus-synthesis-report", "quartus-pnr-report")
        )
        if not needs_synthesis:
            return

        # Primary build artifacts and report side-products
        pn = self.project_name

        # Pro edition uses .syn.rpt/.syn.summary instead of .map.rpt/.map.summary
        syn_prefix = "syn" if self.is_pro else "map"
        map_report = intermediate(output_files / f"{pn}.{syn_prefix}.rpt", "quartus-map-report")
        map_summary = intermediate(output_files / f"{pn}.{syn_prefix}.summary", "quartus-map-summary")
        fit_report = intermediate(output_files / f"{pn}.fit.rpt", "quartus-fit-report")
        fit_summary = intermediate(output_files / f"{pn}.fit.summary", "quartus-fit-summary")
        pin_report = intermediate(output_files / f"{pn}.pin", "quartus-pin-report")
        sta_report = intermediate(output_files / f"{pn}.sta.rpt", "quartus-sta-report")
        sta_summary = intermediate(output_files / f"{pn}.sta.summary", "quartus-sta-summary")
        flow_report = intermediate(output_files / f"{pn}.flow.rpt", "quartus-flow-report")
        sof_resource = intermediate(output_files / f"{pn}.sof", "quartus-sof")
        asm_report = intermediate(output_files / f"{pn}.asm.rpt", "quartus-asm-report")

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

        for dest in self.context.filter_pending(file_type="quartus-jam"):
            jam_resource = intermediate(output_files / f"{pn}.jam", "quartus-jam")
            task.QuartusPfgConvert(
                dispatcher=self,
                name="quartus_sof_jam",
                title="Generate JAM STAPL",
                inputs=[sof_resource],
                outputs=[jam_resource],
            )

        for dest in self.context.filter_pending(file_type="quartus-rbf"):
            rbf_resource = intermediate(output_files / f"{pn}.rbf", "quartus-rbf")
            task.QuartusPfgConvert(
                dispatcher=self,
                name="quartus_sof_rbf",
                title="Generate RBF",
                inputs=[sof_resource],
                outputs=[rbf_resource],
            )
