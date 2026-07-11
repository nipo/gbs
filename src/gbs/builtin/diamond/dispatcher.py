from __future__ import annotations
from pathlib import Path

from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from . import task
from ..ecp5_part import Ecp5Part
from .diamondc import Session


class DiamondDispatcher(BaseDispatcher):
    """Lattice Diamond FPGA synthesis backend

    Drives a persistent diamondc Tcl console through the whole flow:

    1. LPF aggregation: all ecp5-lpf sources are concatenated into a
       single preference file, since a Diamond project has exactly one
       active LPF.

    2. Project init: creates the Diamond project (fixed name "project",
       implementation "impl") with the target device and the aggregated
       LPF, adds HDL sources in dependency order with their libraries,
       sets the top cell and strategy options.

    3. Milestones, each as one task gated by file mtimes:
       - Synthesis + Translate: HDL -> project_impl.ngd (Translate is a
         no-op for LSE, needed for the Synplify engine)
       - Map: NGD + LPF -> project_impl_map.ncd
       - PAR: mapped NCD -> project_impl.ncd (includes trace timing)
       - Export/Bitgen: routed NCD -> project_impl.bit

    Subsequent process() calls add newly appeared LPF files as inputs
    to the aggregation task, so other backends may generate constraints
    between iterations.
    """

    def __init__(
        self,
            context: BuildContext,
            tool_name: str,
            part: Ecp5Part,
            synthesis: str,
            vhdl_std: str,
            strategy: dict[str, str],
    ):
        super().__init__(context, "diamond", tool_name=tool_name)
        self.part = part
        self.synthesis = synthesis
        self.vhdl_std = vhdl_std
        self.strategy = strategy
        self.project_name = "project"
        self.impl_name = "impl"
        # Whether the diamondc session currently has the project
        # loaded; maintained by ProjectInit and Milestone tasks
        self.project_is_open = False
        self._session: Session | None = None

        # Task references (created on first process() call with HDL)
        self._lpf_task: Task | None = None

    @property
    def diamond_path(self) -> Path:
        from ...utils import expand_path
        return expand_path(self.get_tool_option("path"))

    @property
    def impl_dir(self) -> Path:
        return self.context.output_path / self.impl_name

    @property
    def output_base(self) -> str:
        """Base name of the files Diamond generates in the impl directory"""
        return f"{self.project_name}_{self.impl_name}"

    def _get_session(self) -> Session:
        """Get or create shared diamondc session"""
        if self._session is not None:
            return self._session

        diamondc = self.diamond_path / "bin" / "lin64" / "diamondc"
        if not diamondc.exists():
            raise RuntimeError(f"diamondc not found at {diamondc}")

        # diamondc only behaves as an interactive console (with a
        # prompt) when driven through a pty
        self._session = Session(
            argv=[str(diamondc)],
            cwd=self.context.output_path,
            env=self.tool_env or None,
            use_pty=True,
        )

        return self._session

    async def process(self) -> None:
        """Process HDL sources and constraints

        Creates all tasks on first call with HDL sources.
        On subsequent calls, adds new LPF files as inputs to the
        aggregation task.
        """
        if not self.context.project:
            raise ValueError("No project configured")

        has_hdl = bool(list(self.context.filter_pending(file_type=["vhdl", "verilog"])))

        if has_hdl and self._lpf_task is None:
            self.debug("Creating all Diamond build tasks")
            await self._create_all_tasks()
        elif self._lpf_task is not None:
            await self._update_lpf_inputs()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _create_all_tasks(self) -> None:
        """Create all Diamond build tasks on first call"""

        session = self._get_session()
        op = self.context.output_path
        base = self.output_base

        def intermediate(path, file_type, **kwargs):
            return self.context.get_resource(
                path, file_type=file_type,
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name, **kwargs)

        lpf_resource = intermediate(op / "aggregated.lpf", "ecp5-lpf")
        ngd_resource = intermediate(self.impl_dir / f"{base}.ngd", "diamond-netlist",
                                    library="work")
        if self.synthesis == "lse":
            synthesis_log = self.impl_dir / "synthesis.log"
        else:
            synthesis_log = self.impl_dir / f"{base}.srr"
        synthesis_log_resource = intermediate(synthesis_log, "diamond-synthesis-log")
        map_resource = intermediate(self.impl_dir / f"{base}_map.ncd", "diamond-netlist-mapped")
        mrp_resource = intermediate(self.impl_dir / f"{base}.mrp", "diamond-map-report")
        par_resource = intermediate(self.impl_dir / f"{base}.ncd", "diamond-netlist-routed")
        twr_resource = intermediate(self.impl_dir / f"{base}.twr", "diamond-timing-report")
        par_log_resource = intermediate(self.impl_dir / f"{base}.par", "diamond-par-report")
        pad_resource = intermediate(self.impl_dir / f"{base}.pad", "diamond-pad-report")

        bit_resource = self.context.get_resource(
            self.impl_dir / f"{base}.bit", file_type="bitstream",
            typology=ResourceTypology.OUTPUT, generated_by=self.name)

        # Stamp file that marks "the Diamond project description is
        # consistent with the current inputs". Its mtime gates all
        # milestones: when no input changed, every downstream task is
        # skipped before diamondc ever launches.
        init_stamp_resource = self.context.get_stamp(".diamond_project_init.stamp")

        # HDL sources, in dependency order (LSE analyzes in list order)
        hdl_sources = list(self.context.filter_pending(file_type=["vhdl", "verilog"]))

        # LPF aggregation task; inputs are attached by
        # _update_lpf_inputs() on this and subsequent process() calls.
        # Depends on the build definition files so that dropping a
        # constraint file from the sources forces re-aggregation.
        self._lpf_task = task.AggregateLpf(
            dispatcher=self,
            inputs=[],
            outputs=[lpf_resource],
        )
        self.attach_definition_dependencies(self._lpf_task)

        # Project init task. Depends on the aggregated LPF because
        # project creation seeds the LPF file when it does not exist
        # yet; ordering both tasks avoids racing on that file.
        init_task = task.ProjectInit(
            dispatcher=self,
            session=session,
            inputs=list(hdl_sources) + [lpf_resource],
            outputs=[init_stamp_resource],
        )
        self.attach_definition_dependencies(init_task)

        # Synthesis + translation to NGD. Translate is a no-op for LSE
        # (which emits the NGD itself) and converts the EDIF netlist
        # for the Synplify engine. LSE reads the LPF during synthesis.
        task.Milestone(
            dispatcher=self,
            name="diamond_synthesis",
            description="Diamond synthesis",
            milestones=["Synthesis", "Translate"],
            session=session,
            inputs=[init_stamp_resource, lpf_resource],
            outputs=[ngd_resource, synthesis_log_resource],
        )

        task.Milestone(
            dispatcher=self,
            name="diamond_map",
            description="Diamond map",
            milestones=["Map"],
            session=session,
            inputs=[ngd_resource, lpf_resource],
            outputs=[map_resource, mrp_resource],
        )

        # The PAR milestone includes the PARTrace task producing the
        # timing report
        task.Milestone(
            dispatcher=self,
            name="diamond_par",
            description="Diamond place and route",
            milestones=["PAR"],
            session=session,
            inputs=[map_resource],
            outputs=[par_resource, twr_resource, par_log_resource, pad_resource],
        )

        task.Milestone(
            dispatcher=self,
            name="diamond_bitgen",
            description="Diamond bitstream generation",
            milestones=["Export"],
            export_task="Bitgen",
            session=session,
            inputs=[par_resource],
            outputs=[bit_resource],
        )

        # Create report aggregation tasks for any requested aggregate outputs
        for dest in self.context.filter_pending(file_type="diamond-synthesis-report"):
            task.AggregateReport(
                dispatcher=self,
                name="diamond_synthesis_report",
                title="Diamond Synthesis Report",
                inputs=[synthesis_log_resource, mrp_resource],
                outputs=[dest],
            )

        for dest in self.context.filter_pending(file_type="diamond-pnr-report"):
            task.AggregateReport(
                dispatcher=self,
                name="diamond_pnr_report",
                title="Diamond PnR Report",
                inputs=[par_log_resource, pad_resource, twr_resource],
                outputs=[dest],
            )

        await self._update_lpf_inputs()

    async def _update_lpf_inputs(self) -> None:
        """Add new LPF files as inputs to the aggregation task

        Called on every process() iteration so constraint files
        generated by other dispatchers get picked up.
        """
        existing_paths = {r.path for r in self._lpf_task.inputs}
        # Do not consider the file we generate ourselves either
        existing_paths |= {r.path for r in self._lpf_task.outputs}

        for source in self.context.filter_pending(file_type=["ecp5-lpf"]):
            if source.path not in existing_paths:
                self.debug(f"Adding LPF constraint: {source.path}")
                self._lpf_task.add_input(source, consume=False)
