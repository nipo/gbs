"""Lattice Diamond Task implementations"""

from __future__ import annotations
import shutil

from ...build import tcl
from ...build.task import Task, Resource
from ...report_aggregator import TextReport, aggregate_text
from .diamondc import Session, DiamondCommand


class AggregateLpf(Task):
    """Aggregate LPF preference files into a single file

    A Diamond project has exactly one active LPF, so all constraint
    sources are concatenated into one aggregated preference file.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list,
        outputs: list,
    ):
        super().__init__(dispatcher,
            name="diamond_aggregate_lpf",
            inputs=inputs,
            outputs=outputs,
            description="Aggregate Diamond preference files",
        )

    async def work(self) -> None:
        output, = self.outputs

        resources = [r for r in self.inputs
                     if isinstance(r, Resource) and r.file_type == "ecp5-lpf"]

        chunks = []
        for resource in resources:
            content = resource.path.read_text()
            chunks.append(f"# From {resource.path}\n{content}\n")

        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text('\n'.join(chunks))

        self.info(f"Aggregated {len(resources)} preference files to {output.path}")


class ProjectInit(DiamondCommand):
    """Create the Diamond project in the diamondc session

    Recreates the project from scratch (prj_project new refuses to
    overwrite), adds HDL sources in dependency order with their
    libraries, sets the top cell and strategy values, and saves the
    project so later milestones can reopen it from disk.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        session: Session,
        inputs: list,
        outputs: list,
    ):
        super().__init__(dispatcher,
            name="diamond_project_init",
            session=session,
            inputs=inputs,
            outputs=outputs,
            description="Diamond project init",
        )

    def _stale_project_remove(self) -> None:
        """Remove project files from a previous init run"""
        op = self.dispatcher.context.output_path
        name = self.dispatcher.project_name

        ldf = op / f"{name}.ldf"
        if ldf.exists():
            self.debug(f"Removing stale project {ldf}")
            ldf.unlink()
        for sty in op.glob(f"{name}*.sty"):
            sty.unlink()
        if self.dispatcher.impl_dir.exists():
            shutil.rmtree(self.dispatcher.impl_dir)

    async def work(self) -> None:
        dispatcher = self.dispatcher
        context = dispatcher.context
        topcell = context.get_topcell()
        op = context.output_path

        # Close any project left open in the session, then wipe the
        # previous on-disk project state
        await self.command_run(tcl.Command(["catch", tcl.String("prj_project close")]))
        dispatcher.project_is_open = False
        self._stale_project_remove()

        lpf_resource, = (r for r in self.inputs
                         if isinstance(r, Resource) and r.file_type == "ecp5-lpf")

        self.info(f"Creating Diamond project for {dispatcher.part}")
        await self.command_run(tcl.Command([
            "prj_project", "new",
            "-name", dispatcher.project_name,
            "-impl", dispatcher.impl_name,
            "-dev", dispatcher.part.part,
            "-lpf", str(lpf_resource.path),
            "-synthesis", dispatcher.synthesis,
        ]))

        # Add HDL sources in dependency order (LSE analyzes them in
        # list order)
        hdl_inputs = [r for r in self.inputs
                      if isinstance(r, Resource) and r.file_type in ("vhdl", "verilog")]
        total = len(hdl_inputs)
        self.debug(f"Adding {total} HDL source files...")
        for i, resource in enumerate(hdl_inputs):
            library = resource.library or "work"
            self.debug(f"  Adding {resource.path.name} (lib={library})")
            await self.command_run(tcl.Command(
                ["prj_src", "add", "-work", library, str(resource.path)]))
            await self.update_progress(i / total)

        self.debug(f"Setting top module: {topcell}")
        await self.command_run(tcl.Command(["prj_impl", "option", "top", topcell]))

        # Strategy values: VHDL standard, then user overrides.
        # "Strategy1" is the default strategy prj_project new creates.
        strategy_values = {}
        if dispatcher.vhdl_std == "2008":
            engine_prefix = "lse" if dispatcher.synthesis == "lse" else "syn"
            strategy_values[f"{engine_prefix}_vhdl2008"] = "True"
        strategy_values.update(dispatcher.strategy)

        for key, value in strategy_values.items():
            self.debug(f"  Strategy: {key}={value}")
            await self.command_run(tcl.Command(
                ["prj_strgy", "set_value", "-strategy", "Strategy1",
                 tcl.String(f"{key}={value}")]))

        await self.command_run(tcl.Command(["prj_project", "save"]))
        dispatcher.project_is_open = True

        self.info("Project initialization complete")


class Milestone(DiamondCommand):
    """Run Diamond milestones (Synthesis, Translate, Map, PAR, Export...)

    prj_run does not run a milestone's prerequisites, so a task may
    chain several milestones (e.g. Synthesis then Translate).

    Reopens the saved project first when the session does not have it
    loaded (which happens when the init task was up-to-date and got
    skipped).
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        name: str,
        description: str,
        milestones: list[str],
        session: Session,
        inputs: list,
        outputs: list,
        export_task: str | None = None,
    ):
        super().__init__(dispatcher,
            name=name,
            session=session,
            inputs=inputs,
            outputs=outputs,
            description=description,
        )
        self.milestones = milestones
        self.export_task = export_task

    async def work(self) -> None:
        dispatcher = self.dispatcher

        if not dispatcher.project_is_open:
            # output_path may be relative to the GBS process cwd, while
            # diamondc has its own cwd: send an absolute path
            ldf = (dispatcher.context.output_path / f"{dispatcher.project_name}.ldf").resolve()
            await self.command_run(tcl.Command(["prj_project", "open", str(ldf)]))
            dispatcher.project_is_open = True

        for milestone in self.milestones:
            argv = ["prj_run", milestone, "-impl", dispatcher.impl_name]
            if self.export_task:
                argv += ["-task", self.export_task]
            await self.command_run(tcl.Command(argv))


class AggregateReport(Task):
    """Aggregate Diamond report resources into a single tabbed HTML file.

    Tab titles are determined by each input resource's file_type;
    all Diamond reports are plain text.
    """

    TAB_TITLES = {
        "diamond-synthesis-log": "Synthesis",
        "diamond-map-report": "Mapping",
        "diamond-par-report": "Place&Route",
        "diamond-pad-report": "Pads",
        "diamond-timing-report": "Timing",
    }

    def __init__(
        self,
        dispatcher: "Dispatcher",
        name: str,
        title: str,
        inputs: list[Resource],
        outputs: list[Resource],
    ):
        super().__init__(dispatcher,
            name=name,
            inputs=inputs,
            outputs=outputs,
            description=f"Aggregate {title}",
        )
        self.title = title

    async def work(self) -> None:
        tabs = []
        for rsrc in self.inputs:
            tab_title = self.TAB_TITLES.get(rsrc.file_type, rsrc.path.stem)
            text = rsrc.path.read_text(errors="replace")
            tabs.append(TextReport(title=tab_title, text=text))

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(aggregate_text(tabs, title=self.title))
        self.info(f"Aggregated {len(tabs)} reports to {output.path}")
