from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext, BuildFileSet, BuildResource
from . import task
from ...build.task import Task

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

    def __init__(
        self,
        vhdl_std: str = "93c",
        ghdl_tool: str = "ghdl"
    ):
        super().__init__("ghdl", priority=500)
        self.vhdl_std = vhdl_std
        self.ghdl_tool = ghdl_tool  # Tool identifier for lookup
        self._ghdl_executable: str | None = None  # Cached executable path
        self._ghdl_backend_type: str | None = None  # Cached backend type
        self._library_build: dict[str, tuple[BuildResource, Task]] = {}
        self._linker: Task = None

    def _get_ghdl_config(self, context: BuildContext) -> tuple[str, str]:
        """Get GHDL executable and backend type (cached)

        Args:
            context: Build context for tool lookup

        Returns:
            Tuple of (executable_path, backend_type)

        Raises:
            RuntimeError: If GHDL cannot be configured
        """
        # Return cached values if available
        if self._ghdl_executable is not None and self._ghdl_backend_type is not None:
            return self._ghdl_executable, self._ghdl_backend_type

        # Look up GHDL tool configuration (optional, falls back to default)
        tool_config = context.get_tool(self.ghdl_tool, required=False)
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

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables for GHDL

        GHDL is a simulation tool, so it automatically sets target-usage=simulation.
        This allows conditional source filtering based on simulation vs synthesis.

        Also provides ghdl-backend (mcode/gcc/llvm/jit) for backend-specific filtering
        and vhdl-version (normalized to four-digit year) for version-specific code.
        """
        # Get cached config (will detect on first call)
        _, backend_type = self._get_ghdl_config(context)

        # Normalize VHDL version to four-digit year
        vhdl_version = self._normalize_vhdl_version(self.vhdl_std)

        return {
            "target-usage": "simulation",
            "compiler": "ghdl",
            "ghdl-backend": backend_type,  # mcode/gcc/llvm/jit
            "vhdl-version": vhdl_version,  # 1987/1993/2000/2002/2008/2019
        }

    @property
    def vhdl_version(self):
        return "93" if "93" in self.vhdl_std else "08"
    
    def library_build_get(self,
                            context: BuildContext,
                            fileset: BuildFileSet,
                            library: str) -> tuple[BuildResource, Task]:
        try:
            return self._library_build[library]
        except KeyError:
            pass
        
        ghdl_executable, backend_type = self._get_ghdl_config(context)
        workdir = context.output_path / library

        # .cf file that will be generated
        cf_path = workdir / f"{library}-obj{self.vhdl_version}.cf"
        cf_resource = context.get_resource(cf_path, metadata = {
            "file_type": "ghdl-cf",
            "library": library,
            })

        # Track .cf file for dependencies (but don't add to fileset)
        cf_br = BuildResource(
            resource=cf_resource,
            file_type="ghdl-cf",
            library=library,
            is_source=False,
            generated_by=self.name,
        )

        # Create import task (ghdl -i/-a)
        t = task.Import(
            context=context,
            library_name=library,
            ghdl_executable=ghdl_executable,
            vhdl_std=self.vhdl_std,
            inputs=[],
            outputs=[cf_resource],
        )

        fileset.add(cf_br)
        self._library_build[library] = cf_resource, t

        return cf_resource, t

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Compile VHDL design with GHDL"""
        ghdl_executable, backend_type = self._get_ghdl_config(context)

        # Get library dependency graph for correct inter-library dependencies
        # Use transitive closure for GHDL which needs all transitive dependencies in -P flags
        lib_deps_graph = fileset.library_dependency_graph_transitive()

        for lib, deps in lib_deps_graph.items():
            _, user_task = self.library_build_get(context, fileset, lib)

            for d in deps:
                dep_cf, _ = self.library_build_get(context, fileset, d)

                if dep_cf not in user_task.inputs:
                    user_task.inputs.append(dep_cf)
                    user_task.dependency_add(dep_cf)

        
        # Step 2.5: Compile new VHPIDIRECT C files
        self._compile_vhpidirect_sources(
            context,
            fileset,
            ghdl_executable
        )

        if not self._linker:
            # Step 3 & 4: Elaborate/link
            self._linker = self._create_elaboration_tasks(
                context,
                fileset,
                backend_type,
                context.get_topcell(),
                context.get_topcell_library(),
                self.vhdl_version,
                ghdl_executable,
            )

        # Ingress files to linker
        for br in list(fileset.filter(file_type=["ghdl-vhpidirect-lib", "ghdl-cf"])):
            for d in fileset.remove(br.path):
                self._linker.depends_on(d)
            rsrc = context.get_resource(br.path)
            self._linker.inputs.append(rsrc)
            self._linker.dependency_add(rsrc)

        # Get libraries in dependency order
        for library_name, library_files in fileset.by_library_ordered():
            if library_name is None:
                continue

            cf, task = self.library_build_get(context, fileset, library_name)

            for br in library_files:
                if br.file_type != "vhdl":
                    continue

                rsrc = context.get_resource(br.path, metadata = {
                    "file_type": "vhdl",
                    "library": library_name,
                })

                for d in fileset.remove(br.path):
                    task.depends_on(d)
                task.inputs.append(rsrc)
                task.dependency_add(rsrc)
            
    def _compile_vhpidirect_sources(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
        ghdl_executable: str
    ) -> list[BuildResource]:
        """Compile VHPIDIRECT C sources to shared libraries

        Args:
            context: Build context
            fileset: Current build fileset
            ghdl_executable: Path to GHDL executable

        Returns:
            List of compiled library BuildResources
        """
        vhpidirect_libs = []

        # Filter for VHPIDIRECT C files
        for br in list(fileset.filter(file_type=["ghdl-vhpidirect-c"])):
            self.logger.info(f"Compiling VHPIDIRECT C source: {br.path.name}")

            # Create output .so path (stable naming: xxx.c -> xxx.so)
            lib_path = context.output_path / "vhpidirect" / f"{br.path.stem}.so"
            lib_resource = context.get_resource(lib_path, metadata = {
                "file_type": "ghdl-vhpidirect-lib",
                "library": br.library,
                })

            # Create compilation task
            compile_task = task.VHPIDirectCompile(
                context=context,
                ghdl_executable=ghdl_executable,
                compiler="gcc",  # TODO: make configurable
                inputs=[br.resource],
                outputs=[lib_resource],
            )

            # Create BuildResource for the library
            lib_br = BuildResource(
                resource=lib_resource,
                file_type="ghdl-vhpidirect-lib",
                library=br.library,  # Associate with same library as C file
                is_source=False,
                generated_by=self.name,
            )

            for d in fileset.remove(br.path):
                compile_task.dependency_add(d)

            # Add to fileset
            fileset.add(lib_br)
            vhpidirect_libs.append(lib_br)

        if vhpidirect_libs:
            self.logger.info(f"Compiled {len(vhpidirect_libs)} VHPIDIRECT libraries")

        return vhpidirect_libs

    def _create_elaboration_tasks(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
        backend_type: str,
        topcell: str,
        root_library: str,
        vhdl_version: str,
        ghdl_executable: str,
    ) -> Task:
        """Create elaboration/linking tasks for the top entity

        Args:
            ghdl_executable: Path to GHDL executable
            vhpidirect_libs: List of compiled VHPIDIRECT libraries
        """
        if backend_type in ["gcc", "llvm"]:
            final_task_class = task.CompileLink
        else:
            final_task_class = task.MakeElab

        executable_path = context.output_path / "simulator.exe"
        executable_resource = context.get_resource(executable_path)
        
        link_task = final_task_class(
            context=context,
            topcell=topcell,
            ghdl_executable=ghdl_executable,
            vhdl_std=self.vhdl_std,
            root_library=root_library,
            inputs=[],
            outputs=[executable_resource],
        )

        sim_br = BuildResource(
            resource=executable_resource,
            file_type="ghdl-simulator",
            library=root_library,
            is_source=False,
            generated_by=self.name,
        )
        fileset.add(sim_br)

        return link_task
