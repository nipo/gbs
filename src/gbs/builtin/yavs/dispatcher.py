from __future__ import annotations
from pathlib import Path

from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import Task, ResourceTypology
from ...utils import expand_path
from . import task


class YavsDispatcher(BaseDispatcher):
    """Analyze each library with yavs, then elaborate the top level

    Every library lives in its own directory. A library is represented
    by its manifest file rather than by its directory, because
    ``yavs run`` writes its compile cache inside the library directory.
    """

    STD_FLAGS = {
        "1993": "93",
        "2008": "2008",
        "2019": "2019",
    }

    def __init__(self,
        context: BuildContext,
        vhdl_std: str = "1993",
        tool_name: str = "yavs"
    ):
        super().__init__(context, "yavs", tool_name=tool_name)
        try:
            self.std_flag = self.STD_FLAGS[str(vhdl_std)]
        except KeyError:
            raise ValueError(
                f"yavs does not support VHDL standard {vhdl_std!r}, "
                f"use one of {', '.join(self.STD_FLAGS)}") from None
        self.__executable: str | None = None
        self.library_build: dict[str, Task] = {}
        self.elaborate: Task | None = None

    def executable(self) -> str:
        if self.__executable is None:
            self.__executable = str(expand_path(self.get_tool_option("executable", "yavs")))
        return self.__executable

    def library_workdir(self, library: str) -> Path:
        return self.context.output_path / library

    def library_task_get(self, library: str) -> Task:
        try:
            return self.library_build[library]
        except KeyError:
            pass

        manifest = self.context.get_resource(
            self.library_workdir(library) / "yavs-lib.toml",
            file_type="yavs-lib",
            library=library,
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        t = task.Analyze(
            dispatcher=self,
            library_name=library,
            inputs=[],
            outputs=[manifest],
        )
        self.attach_definition_dependencies(t)
        self.library_build[library] = t
        return t

    async def process(self) -> None:
        lib_deps_graph = self.context._pending_library_dependency_graph_transitive()

        for lib, deps in lib_deps_graph.items():
            user_task = self.library_task_get(lib)
            for d in deps:
                dep_manifest, = self.library_task_get(d).outputs_of_type("yavs-lib")
                user_task.add_input(dep_manifest, consume=False)

        if self.elaborate is None:
            self.elaborate = self.elaboration_task_create(
                self.context.get_topcell(),
                self.context.get_topcell_library(),
            )

        for resource in list(self.context.filter_pending(file_type=["yavs-lib"])):
            self.elaborate.add_input(resource, consume=False)

        for library_name, library_files in self.context.get_pending_by_library_ordered():
            if library_name is None:
                continue

            task_obj = self.library_task_get(library_name)
            for resource in library_files:
                if resource.file_type != "vhdl":
                    continue
                task_obj.add_input(resource)

    def elaboration_task_create(self, topcell: str, root_library: str) -> Task:
        simulator = self.context.get_resource(
            self.context.output_path / "simulator.sh",
            file_type="simulator",
            library=root_library,
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        t = task.Elaborate(
            dispatcher=self,
            topcell=topcell,
            root_library=root_library,
            inputs=[],
            outputs=[simulator],
        )
        self.attach_definition_dependencies(t)
        return t


class YavsRunDispatcher(BaseDispatcher):
    """Run a yavs simulator wrapper to produce logs and waveforms"""

    WAVEFORM_TYPES = ["waveform-fst"]

    def __init__(self,
                 context: BuildContext,
                 tool_name: str = "yavs",
                 config: dict | None = None):
        super().__init__(context, "yavs-run", tool_name=tool_name)
        self.run_task: Task | None = None
        self.config = config or {}

    async def process(self) -> None:
        simulators = list(self.context.filter_pending(file_type=["yavs-simulator"]))
        if not simulators:
            return

        if len(simulators) > 1:
            self.warning("More than one simulator available, using first one")
        simulator = simulators[0]

        if self.run_task is None:
            run_dir = self.context.output_path / "simulation_run"
            success = self.context.get_resource(
                run_dir / "simulation.success",
                file_type="simulation-success",
                typology=ResourceTypology.OUTPUT,
                generated_by=self.name,
            )
            log = self.context.get_resource(
                run_dir / "simulation.log",
                file_type="simulation-log",
                typology=ResourceTypology.OUTPUT,
                generated_by=self.name,
            )
            self.run_task = task.RunSimulation(
                dispatcher=self,
                inputs=[simulator],
                outputs=[success, log],
            )
            self.attach_definition_dependencies(self.run_task)

        for resource in list(self.context.filter_pending(file_type=["yavs-lib"])):
            self.run_task.add_input(resource, consume=False)

        if not self.run_task.outputs_of_type(self.WAVEFORM_TYPES):
            for waveform in list(self.context.filter_pending(file_type=self.WAVEFORM_TYPES)):
                if waveform.depends_on:
                    continue
                self.run_task.add_output(waveform)
                break
