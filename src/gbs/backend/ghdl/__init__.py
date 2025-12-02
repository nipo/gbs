"""GHDL Backend for GBS

This module implements the GHDL backend that compiles VHDL designs.
Supports both mcode and compiled (GCC/LLVM) GHDL backends.
"""

from __future__ import annotations
from pathlib import Path
from ...model.build import BuildContext, BuildFileSet, BuildResource
from .backend import GHDLDispatcher
# Backward compatibility
GHDLBackend = GHDLDispatcher

# Pass-based backend implementation
def get_backend():
    """Get the pass-based backend for registry discovery"""
    from ...model.passes import Backend, Pass
    from ...logging import get_logger

    logger = get_logger(__name__)

    class GhdlSimulatePass(Pass):
        """Pass that compiles VHDL designs and creates a simulator executable

        This pass uses GHDL to:
        - Import and analyze VHDL sources by library
        - Elaborate the top-level entity
        - Generate a simulator executable
        """
        name = "simulate"
        input_types = {"vhdl"}
        output_types = {"ghdl-simulator"}

        def contribute_filter_vars(self, config):
            """Indicate this is for simulation"""
            # Get vhdl_std from config, default to "93c"
            vhdl_std = config.get("vhdl_standard", "93c")

            # Normalize VHDL version
            vhdl_version = GHDLBackend._normalize_vhdl_version(vhdl_std)

            return {
                "target-usage": "simulation",
                "compiler": "ghdl",
                "vhdl-version": vhdl_version,
            }

        async def execute(self, context, inputs):
            """Compile VHDL design with GHDL

            Args:
                context: BuildContext for tool lookup and resource management
                inputs: List of BuildResource objects with file_type="vhdl"

            Returns:
                List containing one BuildResource with file_type="ghdl-simulator"
            """
            import subprocess

            if not inputs:
                logger.warning("No VHDL inputs provided to GHDL pass")
                return []

            # Get topcell from context
            topcell = context.get_topcell()
            if not topcell:
                logger.warning("No topcell specified, skipping GHDL elaboration")
                return []

            # Check if simulator already exists
            # Idempotence: Don't rebuild if output exists
            expected_output = Path(topcell)
            if expected_output.exists():
                logger.debug(f"GHDL simulator already exists: {expected_output}, skipping rebuild")
                # Return empty list - the simulator is already in the fileset from iteration 1
                # Returning anything here would cause fileset.add() to increment the serial
                return []

            # Get GHDL configuration from backend_config
            # Note: backend_config should be passed to Pass instances by the executor
            # For now, use defaults
            output_dir = Path("build")
            vhdl_std = "93c"
            ghdl_tool = "ghdl"

            # Create a temporary GHDL backend instance to get config
            temp_backend = GHDLBackend(
                output_dir=output_dir,
                vhdl_std=vhdl_std,
                ghdl_tool=ghdl_tool
            )

            # Get GHDL configuration
            ghdl_executable, backend_type = temp_backend._get_ghdl_config(context)

            # Ensure output directory exists
            output_dir.mkdir(parents=True, exist_ok=True)

            # Get root library
            root_library = context.get_topcell_library()

            # Organize inputs by library
            by_library = {}
            for br in inputs:
                lib = br.library or "work"
                if lib not in by_library:
                    by_library[lib] = []
                by_library[lib].append(br)

            # Get library dependency order
            # For now, use a simple approach: process libraries in the order we see them
            # TODO: Use proper library dependency graph
            from ...model.build import BuildFileSet
            temp_fileset = BuildFileSet(context)
            for br in inputs:
                temp_fileset.add(br)

            lib_order = temp_fileset.libraries_in_dependency_order()

            # Get VHDL version suffix for .cf files
            vhdl_version = "93" if "93" in vhdl_std else "08"

            # Step 1: Import and analyze each library
            cf_dirs = {}
            for library_name in lib_order:
                if library_name not in by_library:
                    continue

                vhdl_files = by_library[library_name]
                logger.info(f"Processing library {library_name} ({len(vhdl_files)} files)")

                # Create workdir for this library
                workdir = output_dir / library_name
                workdir.mkdir(parents=True, exist_ok=True)
                cf_dirs[library_name] = workdir

                # Build -P flags for dependent libraries
                p_flags = []
                for dep_lib in cf_dirs:
                    if dep_lib != library_name:
                        p_flags.append(f"-P{cf_dirs[dep_lib].resolve()}")

                # Collect source files
                sources = [br.path.resolve() for br in vhdl_files]

                # Import (ghdl -i)
                import_cmd = [
                    ghdl_executable, "-i",
                    f"--workdir={workdir.resolve()}",
                    f"--std={vhdl_std}",
                    f"--work={library_name}",
                ] + p_flags + sources

                logger.debug(f"Running: {' '.join(str(x) for x in import_cmd)}")
                result = subprocess.run(import_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"GHDL import failed: {result.stderr}")
                    raise RuntimeError(f"ghdl -i failed for {library_name}")

                # Analyze (ghdl -a)
                analyze_cmd = [
                    ghdl_executable, "-a",
                    f"--workdir={workdir.resolve()}",
                    f"--std={vhdl_std}",
                    f"--work={library_name}",
                ] + p_flags + sources

                logger.debug(f"Running: {' '.join(str(x) for x in analyze_cmd)}")
                result = subprocess.run(analyze_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"GHDL analyze failed: {result.stderr}")
                    raise RuntimeError(f"ghdl -a failed for {library_name}")

            # Step 2: Elaborate the topcell
            root_workdir = output_dir / root_library

            # Build -P flags for all libraries
            p_flags = []
            for lib_name, workdir in cf_dirs.items():
                p_flags.append(f"-P{workdir.resolve()}")

            if backend_type in ["gcc", "llvm"]:
                # Compiled backend: ghdl -c -e
                elab_cmd = [
                    ghdl_executable, "-c", "-O2",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    "-e", topcell
                ]

                logger.debug(f"Running: {' '.join(str(x) for x in elab_cmd)}")
                result = subprocess.run(elab_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"GHDL elaboration failed: {result.stderr}")
                    raise RuntimeError(f"ghdl -c -e failed")

                # For compiled backends, the executable is created directly
                executable_path = Path.cwd() / topcell

            else:
                # mcode/jit backend: ghdl -m -e + wrapper script
                make_cmd = [
                    ghdl_executable, "-m",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    topcell
                ]

                logger.debug(f"Running: {' '.join(str(x) for x in make_cmd)}")
                result = subprocess.run(make_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"GHDL make failed: {result.stderr}")
                    raise RuntimeError(f"ghdl -m failed")

                elab_cmd = [
                    ghdl_executable, "-e",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    topcell
                ]

                logger.debug(f"Running: {' '.join(str(x) for x in elab_cmd)}")
                result = subprocess.run(elab_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"GHDL elaboration failed: {result.stderr}")
                    raise RuntimeError(f"ghdl -e failed")

                # Create wrapper script
                executable_path = Path.cwd() / topcell
                run_cmd = [
                    ghdl_executable, "-r",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    topcell,
                    '"$@"'
                ]

                script_content = f"""#!/bin/sh

exec {' '.join(run_cmd)}
"""
                executable_path.write_text(script_content)
                executable_path.chmod(0o755)

            logger.info(f"Created GHDL simulator: {executable_path}")

            # Create BuildResource for the simulator
            executable_resource = context.get_resource(executable_path)
            sim_br = BuildResource(
                resource=executable_resource,
                file_type="ghdl-simulator",
                library=root_library,
                is_source=False,
                generated_by="ghdl",
            )

            return [sim_br]

    class GhdlBackend(Backend):
        """Backend providing GHDL simulation"""
        passes = [GhdlSimulatePass]

    return GhdlBackend
