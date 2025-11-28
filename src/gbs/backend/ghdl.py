"""GHDL Backend for GBS

This module implements the GHDL backend that compiles VHDL designs.
Supports both mcode and compiled (GCC/LLVM) GHDL backends.
"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from gbs.model.backend import BaseBackend
from gbs.model.build import BuildContext, BuildFileSet, BuildResource, ExecutorTask


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
        self._ghdl_backend_type: str | None = None
        self._processed_libraries: set[str] = set()

    def _detect_ghdl_backend(self, ghdl_executable: str) -> str:
        """Detect GHDL backend type (mcode, gcc, or llvm)

        Args:
            ghdl_executable: Path to GHDL executable

        Returns:
            "mcode", "gcc", or "llvm"

        Raises:
            RuntimeError: If ghdl is not found or version cannot be parsed
        """
        if self._ghdl_backend_type is not None:
            return self._ghdl_backend_type

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
                            self._ghdl_backend_type = backend
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
        """
        return {
            "target-usage": "simulation",
            "compiler": "ghdl",
            "supports_vhdl_2008": True,
        }

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Compile VHDL design with GHDL"""
        import subprocess

        # Look up GHDL tool configuration (optional, falls back to default)
        tool_config = context.get_tool(self.ghdl_tool, required=False)
        if tool_config:
            ghdl_executable = tool_config.get("executable", "ghdl")
            self.logger.debug(f"Using GHDL executable from config: {ghdl_executable}")
        else:
            ghdl_executable = "ghdl"
            self.logger.debug(f"Using default GHDL executable: {ghdl_executable}")

        # Detect GHDL backend type
        backend_type = self._detect_ghdl_backend(ghdl_executable)

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
        analyze_tasks: dict[str, ExecutorTask] = {}

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

            # Track .cf file for dependencies (but don't add to fileset)
            cf_br = BuildResource(
                resource=cf_resource,
                file_type="ghdl_library",
                library=library_name,
                is_source=False,
                generated_by=self.name,
            )
            # Note: .cf files are internal GHDL artifacts, not added to fileset
            cf_files[library_name] = cf_br

            # Collect source file paths in partition dependency order
            # (by_library_ordered already returns files in correct dependency order)
            source_paths = [str(br.path.resolve()) for br in vhdl_files]

            # Build -P flags for dependent libraries
            # Use library dependency graph to get only the actual dependencies
            p_flags = []
            lib_deps = lib_deps_graph.get(library_name, set())
            for dep_lib in lib_deps:
                dep_workdir = self.output_dir / dep_lib
                p_flags.append(f"-P{dep_workdir.resolve()}")
            self.logger.debug(f"Library {library_name} depends on {lib_deps}, p_flags: {p_flags}")

            # Create import task (ghdl -i)
            # Capture loop variables with default arguments
            def make_import_executor(ghdl_exe, wd, std, lib, pflags, srcs, cfp):
                async def import_executor(ctx, inputs):
                    cmd = [
                        ghdl_exe, "-i",
                        f"--workdir={wd.resolve()}",
                        f"--std={std}",
                        f"--work={lib}",
                    ] + pflags + srcs

                    self.logger.info(f"Import: {' '.join(cmd)}")

                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        raise RuntimeError(f"ghdl -i failed for {lib}: {result.stderr}")

                    return [cfp]
                return import_executor

            import_executor = make_import_executor(
                ghdl_executable, workdir, self.vhdl_std, library_name, p_flags[:], source_paths[:], cf_path
            )

            # Import depends on source files and dependent library .cf files
            # Use library dependency graph to get only the actual dependencies
            import_inputs = [br.resource for br in vhdl_files]
            for dep_lib in lib_deps:
                if dep_lib in cf_files:
                    import_inputs.append(cf_files[dep_lib].resource)

            import_task = ExecutorTask(
                context,
                f"ghdl_import_{library_name}",
                inputs=import_inputs,
                outputs=[cf_resource],
                executor=import_executor,
                description=f"GHDL import {library_name}"
            )

            # Create analyze task (ghdl -a)
            # For compiled backends, analyze creates .o files
            # For mcode, it just updates the .cf file
            object_files = []
            if backend_type in ["gcc", "llvm"]:
                for br in vhdl_files:
                    # Object file is named after the source: foo.vhd -> foo.pkg.o or foo.o
                    src_name = br.path.stem  # e.g., "text.pkg" from "text.pkg.vhd"
                    obj_path = workdir / f"{src_name}.o"
                    object_files.append(obj_path)

            # Capture loop variables with default arguments
            def make_analyze_executor(ghdl_exe, wd, std, lib, pflags, srcs, cfp, objfiles):
                async def analyze_executor(ctx, inputs):
                    cmd = [
                        ghdl_exe, "-a",
                        f"--workdir={wd.resolve()}",
                        f"--std={std}",
                        f"--work={lib}",
                    ] + pflags + srcs

                    self.logger.info(f"Analyze: {' '.join(cmd)}")

                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        raise RuntimeError(f"ghdl -a failed for {lib}: {result.stderr}")

                    # Touch .cf to update timestamp
                    cfp.touch()
                    return [cfp] + objfiles
                return analyze_executor

            analyze_executor = make_analyze_executor(
                ghdl_executable, workdir, self.vhdl_std, library_name, p_flags[:], source_paths[:], cf_path, object_files[:]
            )

            # Analyze inputs: source files and dependent library analyze tasks
            # Use library dependency graph to get only the actual dependencies
            analyze_inputs = [import_task] + [br.resource for br in vhdl_files]
            for dep_lib in lib_deps:
                if dep_lib in analyze_tasks:
                    # Depend on the analyze task, not just the .cf file
                    analyze_inputs.append(analyze_tasks[dep_lib])

            # Analyze outputs: .cf file and object files (if compiled backend)
            analyze_output_resources = [cf_resource]
            for obj_path in object_files:
                analyze_output_resources.append(context.get_resource(obj_path))

            analyze_task = ExecutorTask(
                context,
                f"ghdl_analyze_{library_name}",
                inputs=analyze_inputs,
                outputs=analyze_output_resources,
                executor=analyze_executor,
                description=f"GHDL analyze {library_name}"
            )

            # Track analyze task for dependencies
            analyze_tasks[library_name] = analyze_task

            # Add object files to fileset (for compiled backends)
            for obj_path in object_files:
                obj_br = BuildResource(
                    resource=context.get_resource(obj_path),
                    file_type="vhdl_elab",  # Tests expect this file type
                    library=library_name,
                    is_source=False,
                    generated_by=self.name,
                )
                fileset.add(obj_br)

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

        # Build -P flags for all libraries
        p_flags = []
        for lib_name in cf_files.keys():
            lib_workdir = self.output_dir / lib_name
            p_flags.append(f"-P{lib_workdir.resolve()}")

        if backend_type in ["gcc", "llvm"]:
            # Compiled backend: ghdl -c -e
            executable_path = Path.cwd() / topcell
            executable_resource = context.get_resource(executable_path)

            async def compile_link_executor(ctx, inputs):
                cmd = [
                    ghdl_executable, "-c", "-O2",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={self.vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    "-e", topcell
                ]

                self.logger.info(f"Linking: {' '.join(cmd)}")

                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"GHDL -c -e failed: {result.stderr}")

                return [executable_path]

            link_task = ExecutorTask(
                context,
                f"ghdl_link_{topcell}",
                inputs=[cf_files[lib].resource for lib in cf_files],
                outputs=[executable_resource],
                executor=compile_link_executor,
                description=f"GHDL link {topcell}"
            )

            sim_br = BuildResource(
                resource=executable_resource,
                file_type="ghdl_simulator",
                library=root_library,
                is_source=False,
                generated_by=self.name,
            )
            fileset.add(sim_br)

        else:  # mcode/jit
            # Mcode backend: ghdl -m, ghdl -e, then create run script

            # ghdl -m (make)
            async def make_executor(ctx, inputs):
                cmd = [
                    ghdl_executable, "-m",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={self.vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    topcell
                ]

                self.logger.info(f"Make: {' '.join(cmd)}")

                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"GHDL -m failed: {result.stderr}")

                # Return a marker file
                marker = root_workdir / f".{topcell}_made"
                marker.touch()
                return [marker]

            make_marker = context.get_resource(root_workdir / f".{topcell}_made")

            make_task = ExecutorTask(
                context,
                f"ghdl_make_{topcell}",
                inputs=[cf_files[lib].resource for lib in cf_files],
                outputs=[make_marker],
                executor=make_executor,
                description=f"GHDL make {topcell}"
            )

            # ghdl -e (elaborate)
            async def elab_executor(ctx, inputs):
                cmd = [
                    ghdl_executable, "-e",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={self.vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    topcell
                ]

                self.logger.info(f"Elaborate: {' '.join(cmd)}")

                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"GHDL -e failed: {result.stderr}")

                # Create run script
                script_path = Path.cwd() / topcell
                run_cmd = [
                    ghdl_executable, "-r",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={self.vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    topcell,
                    '"$@"'
                ]

                script_content = f"""#!/bin/sh
{' '.join(run_cmd)}
"""
                script_path.write_text(script_content)
                script_path.chmod(0o755)

                return [script_path]

            script_resource = context.get_resource(Path.cwd() / topcell)

            elab_task = ExecutorTask(
                context,
                f"ghdl_elab_{topcell}",
                inputs=[make_marker],
                outputs=[script_resource],
                executor=elab_executor,
                description=f"GHDL elaborate {topcell}"
            )

            sim_br = BuildResource(
                resource=script_resource,
                file_type="ghdl_simulator",
                library=root_library,
                is_source=False,
                generated_by=self.name,
            )
            fileset.add(sim_br)
