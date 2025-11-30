"""GHDL Backend for GBS

This module implements the GHDL backend that compiles VHDL designs.
Supports both mcode and compiled (GCC/LLVM) GHDL backends.
"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ..model.backend import BaseBackend
from ..model.build import BuildContext, BuildFileSet, BuildResource, Task, MessageSubprocess

class GhdlInvocation(MessageSubprocess):
    pass

class ImportTask(Task):
    """GHDL import task (ghdl -i + ghdl -a)"""

    def __init__(
        self,
        context: BuildContext,
        library_name: str,
        ghdl_executable: str,
        workdir: Path,
        vhdl_std: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name=f"ghdl_import_{library_name}",
            inputs=inputs,
            outputs=outputs,
            description=f"GHDL import {library_name}"
        )
        self.library_name = library_name
        self.ghdl_executable = ghdl_executable
        self.workdir = workdir
        self.vhdl_std = vhdl_std

    async def work(self) -> None:
        """Execute GHDL import"""
        import subprocess

        sources = []
        p_flags = []

        for i in self.inputs:
            if i.metadata["file_type"] == "vhdl":
                sources.append(i.path.resolve())
            elif i.metadata["file_type"] == "ghdl-cf":
                p_flags.append(f"-P{i.path.parent.resolve()}")
            else:
                raise ValueError(f"Unknown input type {i}")

        import_process = GhdlInvocation(argv = [
            self.ghdl_executable, "-i",
            f"--workdir={self.workdir.resolve()}",
            f"--std={self.vhdl_std}",
            f"--work={self.library_name}",
        ] + p_flags + sources)

        async for msg in import_process:
            self.add_message_obj(msg)

        if import_process.returncode != 0:
            raise RuntimeError(f"ghdl -i failed for {self.library_name}: {import_process.returncode}")

        analyze_process = GhdlInvocation(argv = [
            self.ghdl_executable, "-a",
            f"--workdir={self.workdir.resolve()}",
            f"--std={self.vhdl_std}",
            f"--work={self.library_name}",
        ] + p_flags + sources)

        async for msg in analyze_process:
            self.add_message_obj(msg)

        if analyze_process.returncode != 0:
            raise RuntimeError(f"ghdl -i failed for {self.library_name}: {analyze_process.returncode}")

class CompileLinkTask(Task):
    """GHDL compile and link task for compiled backends (ghdl -c -e)"""

    def __init__(
        self,
        context: BuildContext,
        topcell: str,
        ghdl_executable: str,
        root_workdir: Path,
        vhdl_std: str,
        root_library: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name=f"ghdl_link_{topcell}",
            inputs=inputs,
            outputs=outputs,
            description=f"GHDL link {topcell}"
        )
        self.topcell = topcell
        self.ghdl_executable = ghdl_executable
        self.root_workdir = root_workdir
        self.vhdl_std = vhdl_std
        self.root_library = root_library

    async def work(self) -> None:
        """Execute GHDL compile and link"""
        import subprocess

        # Build -P flags for all libraries
        p_flags = []
        for res in self.inputs:
            if res.metadata.get("file_type") == "ghdl-cf":
                p_flags.append(f"-P{res.path.parent.resolve()}")

        process = GhdlInvocation(argv = [
            self.ghdl_executable, "-c", "-O2",
            f"--workdir={self.root_workdir.resolve()}",
            f"--std={self.vhdl_std}",
        ] + p_flags + [
            f"--work={self.root_library}",
            "-e", self.topcell
        ])

        async for msg in process:
            self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(f"ghdl -c failed: {process.returncode}")

class MakeElabTask(Task):
    """GHDL make task for mcode/jit backends (ghdl -m / -e + wrapper generator)"""

    def __init__(
        self,
        context: BuildContext,
        topcell: str,
        ghdl_executable: str,
        root_workdir: Path,
        vhdl_std: str,
        root_library: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name=f"ghdl_make_{topcell}",
            inputs=inputs,
            outputs=outputs,
            description=f"GHDL make {topcell}"
        )
        self.topcell = topcell
        self.ghdl_executable = ghdl_executable
        self.root_workdir = root_workdir
        self.vhdl_std = vhdl_std
        self.root_library = root_library

    async def work(self) -> None:
        """Execute GHDL compile and link"""
        import subprocess

        assert len(self.outputs) == 1

        # Build -P flags for all libraries
        p_flags = []
        for res in self.inputs:
            if res.metadata.get("file_type") == "ghdl-cf":
                p_flags.append(f"-P{res.path.parent.resolve()}")

        process = GhdlInvocation(argv = [
            self.ghdl_executable, "-m",
            f"--workdir={self.root_workdir.resolve()}",
            f"--std={self.vhdl_std}",
        ] + p_flags + [
            f"--work={self.root_library}",
            self.topcell
        ])

        async for msg in process:
            self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(f"ghdl -m failed: {process.returncode}")

        process = GhdlInvocation(argv = [
            self.ghdl_executable, "-e",
            f"--workdir={self.root_workdir.resolve()}",
            f"--std={self.vhdl_std}",
        ] + p_flags + [
            f"--work={self.root_library}",
            self.topcell
        ])

        async for msg in process:
            self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(f"ghdl -e failed: {process.returncode}")

        # Create run script
        run_cmd = [
            self.ghdl_executable, "-r",
            f"--workdir={self.root_workdir.resolve()}",
            f"--std={self.vhdl_std}",
        ] + p_flags + [
            f"--work={self.root_library}",
            self.topcell,
            '"$@"'
        ]

        script_content = f"""#!/bin/sh

exec {' '.join(run_cmd)}
"""
        self.outputs[0].path.write_text(script_content)
        self.outputs[0].path.chmod(0o755)

class GHDLBackend(BaseBackend):
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
        output_dir: Path | str | None = None,
        vhdl_std: str = "93c",
        ghdl_tool: str = "ghdl"
    ):
        super().__init__("ghdl", priority=500)
        self.output_dir = Path(output_dir) if output_dir is not None else Path("build")
        self.vhdl_std = vhdl_std
        self.ghdl_tool = ghdl_tool  # Tool identifier for lookup
        self._ghdl_executable: str | None = None  # Cached executable path
        self._ghdl_backend_type: str | None = None  # Cached backend type
        self._processed_libraries: set[str] = set()

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

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Compile VHDL design with GHDL"""
        import subprocess

        # Get GHDL configuration (cached after first call)
        ghdl_executable, backend_type = self._get_ghdl_config(context)

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Get VHDL version suffix for .cf files
        vhdl_version = "93" if "93" in self.vhdl_std else "08"

        # Get libraries in dependency order
        by_library = fileset.by_library_ordered()

        # Get library dependency graph for correct inter-library dependencies
        # Use transitive closure for GHDL which needs all transitive dependencies in -P flags
        lib_deps_graph = fileset.library_dependency_graph_transitive()

        # Track .cf files and analyze tasks for dependencies
        cf_files: dict[str, BuildResource] = {}

        # Step 1 & 2: Import and analyze each library
        for library_name, library_files in by_library:
            if library_name is None:
                continue

            # Filter for VHDL files only
            vhdl_files = [br for br in library_files if br.file_type == "vhdl"]
            if not vhdl_files:
                continue

            self.logger.debug(f"Library {library_name} file order from by_library_ordered: {[br.path.name for br in vhdl_files]}")

            if library_name in self._processed_libraries:
                continue

            self.logger.info(f"Processing library {library_name} ({len(vhdl_files)} files)")

            # Create workdir for this library
            workdir = self.output_dir / library_name
            workdir.mkdir(parents=True, exist_ok=True)

            # .cf file that will be generated
            cf_path = workdir / f"{library_name}-obj{vhdl_version}.cf"
            cf_resource = context.get_resource(cf_path)
            cf_resource.metadata["file_type"] = "ghdl-cf"

            # Track .cf file for dependencies (but don't add to fileset)
            cf_br = BuildResource(
                resource=cf_resource,
                file_type="ghdl-cf",
                library=library_name,
                is_source=False,
                generated_by=self.name,
            )

            # Note: .cf files are internal GHDL artifacts, not added to fileset
            cf_files[library_name] = cf_br

            inputs = []

            # Collect source file paths in partition dependency order
            # (by_library_ordered already returns files in correct dependency order)
            for br in vhdl_files:
                inputs.append(br.resource)

            # Import depends on source files and dependent library .cf
            # files Use library dependency graph to get only the
            # actual dependencies
            lib_deps = lib_deps_graph.get(library_name, set())
            for dep_lib in cf_files:
                if dep_lib == library_name:
                    continue
                inputs.append(cf_files[dep_lib].resource)

            # Create import task (ghdl -i)
            import_task = ImportTask(
                context=context,
                library_name=library_name,
                ghdl_executable=ghdl_executable,
                workdir=workdir,
                vhdl_std=self.vhdl_std,
                inputs=inputs,
                outputs=[cf_resource],
            )

            self._processed_libraries.add(library_name)

        # Step 3 & 4: Elaborate/link
        topcell = context.get_topcell()
        if topcell and cf_files:
            # Get the root library from context
            root_library = context.get_topcell_library()

            if root_library:
                self._create_elaboration_tasks(
                    context,
                    fileset,
                    backend_type,
                    topcell,
                    root_library,
                    cf_files,
                    vhdl_version,
                    ghdl_executable
                )

    def _create_elaboration_tasks(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
        backend_type: str,
        topcell: str,
        root_library: str,
        cf_files: dict[str, BuildResource],
        vhdl_version: str,
        ghdl_executable: str
    ):
        """Create elaboration/linking tasks for the top entity

        Args:
            ghdl_executable: Path to GHDL executable
        """
        import subprocess

        root_workdir = self.output_dir / root_library

        if backend_type in ["gcc", "llvm"]:
            final_task_class = CompileLinkTask
        else:
            final_task_class = MakeElabTask

        executable_path = Path.cwd() / topcell
        executable_resource = context.get_resource(executable_path)

        link_task = final_task_class(
            context=context,
            topcell=topcell,
            ghdl_executable=ghdl_executable,
            root_workdir=root_workdir,
            vhdl_std=self.vhdl_std,
            root_library=root_library,
            inputs=[x.resource for x in cf_files.values()],
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
