"""GHDL Dispatchers - VHDL analysis and simulation"""

from __future__ import annotations
import subprocess
from pathlib import Path

from ...utils import expand_path
from ...logging import get_logger
from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import Task, ResourceTypology
from . import task

logger = get_logger(__name__)


class GHDLBaseDispatcher(BaseDispatcher):
    """Base dispatcher for GHDL-based dispatchers with common functionality

    Provides shared functionality for GHDL analysis and simulation dispatchers:
    - GHDL executable path lookup and caching
    - GHDL backend type detection (mcode, gcc, llvm, jit)
    - Library working directory management
    - VHDL version string conversion
    """

    def __init__(
        self,
        context: BuildContext,
        name: str,
        vhdl_std: str = "1993",
        tool_name: str = "ghdl"
    ):
        """Initialize GHDL base dispatcher

        Args:
            context: Build context
            name: Dispatcher name
            vhdl_std: VHDL standard year (e.g., "1993", "2008")
            tool_name: Tool identifier for lookup (default: "ghdl")
        """
        super().__init__(context, name, tool_name=tool_name)
        self.vhdl_std = vhdl_std
        self._ghdl_executable: str | None = None
        self._ghdl_backend_type: str | None = None

        # Convert VHDL standard to GHDL version string
        self.ghdl_vhdl_version = self._convert_vhdl_version(vhdl_std)

    @staticmethod
    def _convert_vhdl_version(vhdl_std: str) -> str:
        """Convert VHDL standard year to GHDL version string

        Args:
            vhdl_std: VHDL standard year (e.g., "1993", "2008")

        Returns:
            GHDL version string (e.g., "93c", "08")
        """
        year_map = {
            "1987": "93c",
            "1993": "93c",
            "2000": "00",
            "2002": "02",
            "2008": "08",
            "2019": "19",
        }
        return year_map.get(vhdl_std, "93c")

    def _get_ghdl_executable(self) -> str:
        """Get GHDL executable path (cached)

        Returns:
            Executable path
        """
        if self._ghdl_executable is None:
            tool_config = self.context.get_tool(self.tool_name, required=False)
            if tool_config:
                ghdl_executable = tool_config.get("executable", "ghdl")
                # Expand ~ and environment variables in executable path
                ghdl_executable = str(expand_path(ghdl_executable))
                logger.debug(f"Using GHDL executable from config: {ghdl_executable}")
            else:
                ghdl_executable = "ghdl"
                logger.debug(f"Using default GHDL executable: {ghdl_executable}")

            self._ghdl_executable = ghdl_executable

        return self._ghdl_executable

    def _detect_ghdl_backend(self, ghdl_executable: str) -> str:
        """Detect GHDL backend type (mcode, gcc, llvm, or jit)

        Args:
            ghdl_executable: Path to GHDL executable

        Returns:
            "mcode", "gcc", "llvm", or "jit"

        Raises:
            RuntimeError: If ghdl is not found or version cannot be parsed
        """
        try:
            result = subprocess.run(
                [ghdl_executable, "--version"],
                capture_output=True,
                text=True,
                check=True
            )

            # Look for "code generator" line
            # Format can be: " llvm 19.1.7 code generator" or "code generator: mcode"
            # Note: "LLVM JIT" should be detected as "jit", not "llvm"
            for line in result.stdout.split('\n'):
                if 'code generator' in line.lower():
                    line_lower = line.lower()
                    # Check all words in the line for known backends
                    # Priority order: jit > mcode > gcc > llvm
                    # (JIT must come before llvm since "LLVM JIT" contains both words)
                    words = line_lower.split()
                    for backend in ['jit', 'mcode', 'gcc', 'llvm']:
                        if backend in words:
                            logger.info(f"Detected GHDL backend: {backend}")
                            return backend

            raise RuntimeError("Could not detect GHDL backend type from --version output")

        except FileNotFoundError:
            raise RuntimeError(f"GHDL executable not found: {ghdl_executable}")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"GHDL --version failed: {e}")

    def _get_ghdl_config(self) -> tuple[str, str]:
        """Get GHDL executable and backend type (cached)

        Returns:
            Tuple of (executable_path, backend_type)

        Raises:
            RuntimeError: If GHDL cannot be configured
        """
        # Return cached values if available
        if self._ghdl_executable is not None and self._ghdl_backend_type is not None:
            return self._ghdl_executable, self._ghdl_backend_type

        # Get executable
        self._ghdl_executable = self._get_ghdl_executable()

        # Detect backend type
        self._ghdl_backend_type = self._detect_ghdl_backend(self._ghdl_executable)

        return self._ghdl_executable, self._ghdl_backend_type

    def library_workdir(self, library: str) -> Path:
        """Get work directory for a library

        Args:
            library: Library name

        Returns:
            Path to library work directory
        """
        return self.context.output_path / library


class GHDLAnalyzeDispatcher(GHDLBaseDispatcher):
    """GHDL analysis dispatcher that converts VHDL to library intermediates

    This dispatcher only performs analysis (ghdl -i/-a) and produces
    .cf library intermediate files. These can be consumed by:
    - GHDLSimulateDispatcher for simulation
    - Yosys+GHDL for synthesis
    - Other tools that can use GHDL libraries
    """

    def __init__(self,
        context: BuildContext,
        vhdl_std: str = "1993",
        tool_name: str = "ghdl"
    ):
        super().__init__(context, "ghdl-analyze", vhdl_std, tool_name)
        self._library_build: dict[str, tuple['Resource', Task]] = {}

    def library_build_get(self, library: str) -> tuple['Resource', Task]:
        """Get or create library build task and resource

        Args:
            library: Library name

        Returns:
            Tuple of (cf resource, import task)
        """
        try:
            return self._library_build[library]
        except KeyError:
            pass

        workdir = self.library_workdir(library)

        # .cf file that will be generated
        cf_path = workdir / f"{library}-obj{self.ghdl_vhdl_version.rstrip('c')}.cf"
        cf_resource = self.context.get_resource(
            cf_path,
            file_type="ghdl-cf",
            library=library,
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        # Create import task (ghdl -i/-a)
        t = task.Import(
            dispatcher=self,
            library_name=library,
            inputs=[],
            outputs=[cf_resource],
        )

        # Add to pending queue
        self.context.add_pending(cf_resource)
        self._library_build[library] = cf_resource, t

        return cf_resource, t

    async def process(self) -> None:
        """Analyze VHDL sources into library intermediates"""
        # Get library dependency graph for correct inter-library dependencies
        # Use transitive closure for GHDL which needs all transitive dependencies in -P flags
        lib_deps_graph = self.context._pending_library_dependency_graph_transitive()

        for lib, deps in lib_deps_graph.items():
            _, user_task = self.library_build_get(lib)

            for d in deps:
                dep_cf, _ = self.library_build_get(d)

                if dep_cf not in user_task.inputs:
                    user_task.inputs.append(dep_cf)
                    user_task.dependency_add(dep_cf)

        # Get libraries in dependency order and process VHDL sources
        for library_name, library_files in self.context.get_pending_by_library_ordered():
            if library_name is None:
                continue

            cf, task_obj = self.library_build_get(library_name)

            for resource in library_files:
                if resource.file_type != "vhdl":
                    continue

                # Remove from pending (consuming the source)
                # Add dependents as task dependencies to ensure proper execution order
                dependents = self.context.remove_pending(resource.path)
                task_obj.inputs.append(resource)
                task_obj.dependency_add(resource)
                for dep in dependents:
                    task_obj.dependency_add(dep)


class GHDLSimulateDispatcher(GHDLBaseDispatcher):
    """GHDL simulation dispatcher that creates simulator executable

    This dispatcher takes GHDL library intermediates (.cf files) and:
    1. Compiles VHPIDIRECT C sources (if any)
    2. Elaborates/links the top-level entity
    3. Creates a simulator executable

    Supports both mcode and compiled (GCC/LLVM) GHDL backends.

    Priority: 500 (main compilation)
    """

    def __init__(self,
        context: BuildContext,
        vhdl_std: str = "1993",
        tool_name: str = "ghdl"
    ):
        super().__init__(context, "ghdl-simulate", vhdl_std, tool_name)
        self._linker: Task = None

    async def process(self) -> None:
        """Create simulator executable from GHDL library intermediates"""
        # Step 1: Compile VHPIDIRECT C sources
        self._compile_vhpidirect_sources()

        if not self._linker:
            # Step 2: Create elaboration/link task
            self._linker = self._create_elaboration_tasks(
                self.context.get_topcell(),
                self.context.get_topcell_library(),
            )

        # Ingress files to linker (ghdl-cf and ghdl-vhpidirect-lib)
        for resource in list(self.context.filter_pending(file_type=["ghdl-vhpidirect-lib", "ghdl-cf"])):
            # Remove from pending (consuming the intermediate files)
            dependents = self.context.remove_pending(resource.path)
            self._linker.inputs.append(resource)
            self._linker.dependency_add(resource)
            for dep in dependents:
                self._linker.dependency_add(dep)

    def _compile_vhpidirect_sources(self):
        """Compile VHPIDIRECT C sources to shared libraries"""
        vhpidirect_count = 0

        # Filter for VHPIDIRECT C files
        for resource in list(self.context.filter_pending(file_type=["ghdl-vhpidirect-c"])):
            self.info(f"Compiling VHPIDIRECT C source: {resource.path.name}")

            # Create output .so path (stable naming: xxx.c -> xxx.so)
            lib_path = self.context.output_path / "vhpidirect" / f"{resource.path.stem}.so"
            lib_resource = self.context.get_resource(
                lib_path,
                file_type="ghdl-vhpidirect-lib",
                library=resource.library,
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name,
            )

            # Create compilation task
            compile_task = task.VHPIDirectCompile(
                self,
                compiler="gcc",  # TODO: make configurable
                inputs=[resource],
                outputs=[lib_resource],
            )

            # Remove C source from pending (it's consumed by the task)
            dependents = self.context.remove_pending(resource.path)
            for dep in dependents:
                compile_task.dependency_add(dep)

            # Add output library to pending queue
            self.context.add_pending(lib_resource)
            vhpidirect_count += 1

        if vhpidirect_count:
            self.info(f"Compiled {vhpidirect_count} VHPIDIRECT libraries")

    def _create_elaboration_tasks(self,
        topcell: str,
        root_library: str,
    ) -> Task:
        """Create elaboration/linking tasks for the top entity

        Args:
            topcell: Top-level entity name
            root_library: Root library name

        Returns:
            Elaboration task
        """
        _, backend_type = self._get_ghdl_config()

        if backend_type in ["gcc", "llvm"]:
            final_task_class = task.CompileLink
        else:
            final_task_class = task.MakeElab

        executable_path = self.context.output_path / "simulator.exe"
        executable_resource = self.context.get_resource(
            executable_path,
            file_type="ghdl-simulator",
            library=root_library,
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        link_task = final_task_class(
            dispatcher=self,
            topcell=topcell,
            root_library=root_library,
            inputs=[],
            outputs=[executable_resource],
        )

        # Add simulator to pending queue
        self.context.add_pending(executable_resource)

        return link_task


class GHDLRunDispatcher(BaseDispatcher):
    """GHDL run dispatcher that executes simulator to produce waveforms and logs

    This dispatcher takes a ghdl-simulator executable and runs it to produce:
    - Waveform files (VCD, GHW, or FST)
    - Simulation logs (captured from stdout/stderr)

    Priority: 900 (post-processing/execution)
    """

    def __init__(self,
                 context: BuildContext,
                 tool_name: str = "ghdl",
                 config: dict = {}):
        super().__init__(context, "ghdl-run",
                         tool_name = tool_name)
        self._run_task = None
        self.config = config

    async def process(self) -> None:
        """Create simulation run tasks from simulator executable and desired outputs"""

        # Find simulator executable
        simulator_resources = list(self.context.filter_pending(file_type=["ghdl-simulator"]))
        if not simulator_resources:
            self.debug("No simulator to run")
            return

        # Should only be one simulator per build
        if len(simulator_resources) > 1:
            self.warning("More than one simulator available, using first one")

        simulator = simulator_resources[0]

        # Collect all waveform and log outputs
        if not self._run_task:
            success_resource = self.context.get_resource(
                self.context.output_path / "simulation_run" / "simulation.success",
                file_type="simulation-success",
                typology=ResourceTypology.OUTPUT,
                generated_by=self.name,
            )
            self.context.add_pending(success_resource)

            log_resource = self.context.get_resource(
                self.context.output_path / "simulation_run" / "simulation.log",
                file_type="simulation-log",
                typology=ResourceTypology.OUTPUT,
                generated_by=self.name,
            )
            self.context.add_pending(log_resource)

            self._run_task = task.RunSimulation(
                dispatcher=self,
                inputs=[simulator],
                outputs=[success_resource, log_resource]
            )

        if not self._run_task.outputs_of_type(["waveform-vcd", "waveform-ghw", "waveform-fst"]):
            for waveform in list(self.context.filter_pending(file_type=["waveform-vcd", "waveform-ghw", "waveform-fst"])):
                if waveform.depends_on:
                    continue

                self._run_task.outputs.append(waveform)
                waveform.dependency_add(self._run_task)
                break
