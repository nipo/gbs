from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import Task, ResourceTypology
from . import task

class GHDLDispatcher(BaseDispatcher):
    """GHDL backend that compiles VHDL designs

    Supports both mcode and compiled (GCC/LLVM) GHDL backends.

    Build process:
    1. Detect GHDL backend type (mcode vs compiled)
    2. Import and analyze each library in dependency order
    3. For compiled: link with ghdl -c -e
    4. For mcode: make/elaborate with ghdl -m -e, generate run script

    Priority: 500 (main compilation)
    """

    def __init__(self,
        context: BuildContext,
        vhdl_std: str = "93c",
        ghdl_tool: str = "ghdl"
    ):
        super().__init__(context, "ghdl", priority=500)
        self.vhdl_std = vhdl_std
        self.ghdl_tool = ghdl_tool  # Tool identifier for lookup
        self._ghdl_executable: str | None = None  # Cached executable path
        self._ghdl_backend_type: str | None = None  # Cached backend type
        self._library_build: dict[str, tuple['Resource', Task]] = {}
        self._linker: Task = None

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

        # Look up GHDL tool configuration (optional, falls back to default)
        tool_config = self.context.get_tool(self.ghdl_tool, required=False)
        if tool_config:
            ghdl_executable = tool_config.get("executable", "ghdl")
            self.logger.debug(f"Using GHDL executable from config: {ghdl_executable}")
        else:
            ghdl_executable = "ghdl"
            self.logger.debug(f"Using default GHDL executable: {ghdl_executable}")

        # Detect backend type
        backend_type = self._detect_ghdl_backend(ghdl_executable)

        # Cache both values
        self._ghdl_executable = ghdl_executable
        self._ghdl_backend_type = backend_type

        return ghdl_executable, backend_type

    @staticmethod
    def _normalize_vhdl_version(vhdl_std: str) -> str:
        """Normalize VHDL standard to four-digit year format

        Args:
            vhdl_std: GHDL standard string (e.g., "93c", "08", "2008")

        Returns:
            Four-digit year string (e.g., "1993", "2008")
        """
        # Strip any suffix characters (like 'c' for common extensions)
        std = vhdl_std.rstrip('c')

        # Map two-digit years to four-digit years
        year_map = {
            "87": "1987",
            "93": "1993",
            "00": "2000",
            "02": "2002",
            "08": "2008",
            "19": "2019",
        }

        # If already four digits, return as-is
        if len(std) == 4 and std.isdigit():
            return std

        # If two digits, look up in map
        if std in year_map:
            return year_map[std]

        # Default to 1993 if unknown
        return "1993"

    def _detect_ghdl_backend(self, ghdl_executable: str) -> str:
        """Detect GHDL backend type (mcode, gcc, llvm, or jit)

        Args:
            ghdl_executable: Path to GHDL executable

        Returns:
            "mcode", "gcc", "llvm", or "jit"

        Raises:
            RuntimeError: If ghdl is not found or version cannot be parsed
        """
        # Note: Don't check cache here - _get_ghdl_config handles caching
        import subprocess

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
                            self.logger.info(f"Detected GHDL backend: {backend}")
                            return backend

            raise RuntimeError("Could not detect GHDL backend type from --version output")

        except FileNotFoundError:
            raise RuntimeError(f"GHDL executable not found: {ghdl_executable}")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"GHDL --version failed: {e}")

    @property
    def vhdl_version(self):
        return "93" if "93" in self.vhdl_std else "08"
    
    def library_build_get(self, library: str) -> tuple['Resource', Task]:
        try:
            return self._library_build[library]
        except KeyError:
            pass

        ghdl_executable, backend_type = self._get_ghdl_config()
        workdir = self.context.output_path / library

        # .cf file that will be generated
        cf_path = workdir / f"{library}-obj{self.vhdl_version}.cf"
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
            ghdl_executable=ghdl_executable,
            vhdl_std=self.vhdl_std,
            inputs=[],
            outputs=[cf_resource],
        )

        # Add to pending queue
        self.context.add_pending(cf_resource)
        self._library_build[library] = cf_resource, t

        return cf_resource, t

    async def process(self) -> None:
        """Compile VHDL design with GHDL"""
        ghdl_executable, backend_type = self._get_ghdl_config()

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


        # Step 2.5: Compile new VHPIDIRECT C files
        self._compile_vhpidirect_sources(context, ghdl_executable)

        if not self._linker:
            # Step 3 & 4: Elaborate/link
            self._linker = self._create_elaboration_tasks(
                context,
                backend_type,
                self.context.get_topcell(),
                self.context.get_topcell_library(),
                self.vhdl_version,
                ghdl_executable,
            )

        # Ingress files to linker
        for resource in list(self.context.filter_pending(file_type=["ghdl-vhpidirect-lib", "ghdl-cf"])):
            # Remove from pending (consuming the intermediate files)
            dependents = self.context.remove_pending(resource.path)
            self._linker.inputs.append(resource)
            self._linker.dependency_add(resource)
            for dep in dependents:
                self._linker.dependency_add(dep)

        # Get libraries in dependency order
        for library_name, library_files in self.context.get_pending_by_library_ordered():
            if library_name is None:
                continue

            cf, task_obj = self.library_build_get(context, library_name)

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
            
    def _compile_vhpidirect_sources(self,
        ghdl_executable: str
    ):
        """Compile VHPIDIRECT C sources to shared libraries

        Args:
            context: Build context
            ghdl_executable: Path to GHDL executable
        """
        vhpidirect_count = 0

        # Filter for VHPIDIRECT C files
        for resource in list(self.context.filter_pending(file_type=["ghdl-vhpidirect-c"])):
            self.logger.info(f"Compiling VHPIDIRECT C source: {resource.path.name}")

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
                context=context,
                ghdl_executable=ghdl_executable,
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
            self.logger.info(f"Compiled {vhpidirect_count} VHPIDIRECT libraries")

    def _create_elaboration_tasks(self,
        backend_type: str,
        topcell: str,
        root_library: str,
        vhdl_version: str,
        ghdl_executable: str,
    ) -> Task:
        """Create elaboration/linking tasks for the top entity

        Args:
            context: Build context
            backend_type: GHDL backend type (gcc, llvm, mcode, jit)
            topcell: Top-level entity name
            root_library: Root library name
            vhdl_version: VHDL version string
            ghdl_executable: Path to GHDL executable
        """
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
            context=context,
            topcell=topcell,
            ghdl_executable=ghdl_executable,
            vhdl_std=self.vhdl_std,
            root_library=root_library,
            inputs=[],
            outputs=[executable_resource],
        )

        # Add simulator to pending queue
        self.context.add_pending(executable_resource)

        return link_task
