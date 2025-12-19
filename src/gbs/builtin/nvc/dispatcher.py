from __future__ import annotations
from typing import Any
from pathlib import Path

from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import Task, ResourceTypology
from ...utils import expand_path
from . import task

class NVCDispatcher(BaseDispatcher):
    """NVC backend that compiles VHDL designs

    NVC uses a simpler workflow than GHDL:
    1. Analyze (-a) each library in dependency order
    2. Elaborate (-e) the top-level design unit
    3. Generate executable wrapper script
    """

    def __init__(self,
        context: BuildContext,
        vhdl_std: str = "1993",
        nvc_tool: str = "nvc"
    ):
        super().__init__(context, "nvc", tool_name=nvc_tool)
        self.vhdl_std = vhdl_std
        self.nvc_tool = nvc_tool  # Tool identifier for lookup
        self._nvc_executable: str | None = None  # Cached executable path
        self._library_build: dict[str, tuple['Resource', Task]] = {}
        self._linker: Task = None

    def _get_nvc_config(self) -> str:
        """Get NVC executable path (cached)

        Returns:
            Executable path

        Raises:
            RuntimeError: If NVC cannot be configured
        """
        # Return cached value if available
        if self._nvc_executable is not None:
            return self._nvc_executable

        # Look up NVC tool configuration (optional, falls back to default)
        tool_config = self.context.get_tool(self.nvc_tool, required=False)
        if tool_config:
            nvc_executable = tool_config.get("executable", "nvc")
            # Expand ~ and environment variables in executable path
            nvc_executable = str(expand_path(nvc_executable))
            self.debug(f"Using NVC executable from config: {nvc_executable}")
        else:
            nvc_executable = "nvc"
            self.debug(f"Using default NVC executable: {nvc_executable}")

        # Cache the value
        self._nvc_executable = nvc_executable

        return nvc_executable

    def library_workdir(self, library: str) -> Path:
        """Get work directory for a library

        Args:
            library: Library name

        Returns:
            Path to library work directory
        """
        return self.context.output_path / library

    def library_build_get(self, library: str) -> tuple['Resource', Task]:
        """Get or create library build task and resource

        Args:
            library: Library name

        Returns:
            Tuple of (library resource, analyze task)
        """
        try:
            return self._library_build[library]
        except KeyError:
            pass

        workdir = self.library_workdir(library)

        # Library marker file (NVC creates _NVC_LIB file in work directory)
        lib_marker = workdir / "_NVC_LIB"
        lib_resource = self.context.get_resource(
            lib_marker,
            file_type="nvc-lib",
            library=library,
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        # Create analyze task (nvc -a)
        t = task.Analyze(
            dispatcher=self,
            library_name=library,
            inputs=[],
            outputs=[lib_resource],
        )

        # Add to pending queue
        self._library_build[library] = lib_resource, t

        return lib_resource, t

    async def process(self) -> None:
        """Compile VHDL design with NVC"""
        # Get library dependency graph for correct inter-library dependencies
        # Use transitive closure for NVC which needs all transitive dependencies
        lib_deps_graph = self.context._pending_library_dependency_graph_transitive()

        for lib, deps in lib_deps_graph.items():
            _, user_task = self.library_build_get(lib)

            for d in deps:
                dep_lib, _ = self.library_build_get(d)

                if dep_lib not in user_task.inputs:
                    user_task.add_input(dep_lib)

        if not self._linker:
            # Create elaboration task
            self._linker = self._create_elaboration_task(
                self.context.get_topcell(),
                self.context.get_topcell_library(),
            )

        # Ingress library files to linker
        for resource in list(self.context.filter_pending(file_type=["nvc-lib"])):
            # These are INTERMEDIATE resources - don't consume them (consume=False)
            # so they remain available for other tasks
            self._linker.add_input(resource, consume=False)

        # Get libraries in dependency order
        for library_name, library_files in self.context.get_pending_by_library_ordered():
            if library_name is None:
                continue

            lib_marker, task_obj = self.library_build_get(library_name)

            for resource in library_files:
                if resource.file_type != "vhdl":
                    continue

                # Remove from pending (consuming the source)
                # Add dependents as task dependencies to ensure proper execution order
                task_obj.add_input(resource)

    def _create_elaboration_task(self,
        topcell: str,
        root_library: str,
    ) -> Task:
        """Create elaboration task for the top entity

        Args:
            topcell: Top-level entity name
            root_library: Root library name

        Returns:
            Elaboration task
        """
        executable_path = self.context.output_path / "simulator.exe"
        executable_resource = self.context.get_resource(
            executable_path,
            file_type="nvc-simulator",
            library=root_library,
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        elab_task = task.Elaborate(
            dispatcher=self,
            topcell=topcell,
            root_library=root_library,
            inputs=[],
            outputs=[executable_resource],
        )

        # Add simulator to pending queue

        return elab_task
