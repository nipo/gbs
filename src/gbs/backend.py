"""Backend System for GBS

This module implements the unified backend system where all backends
(preprocessing, transpilation, and main compilation) are equal participants
in an iterative transformation process.

Key concepts:
- Backend: Transforms BuildFileSet iteratively
- BackendRegistry: Manages backends and their priorities
- Iteration loop: Runs backends until fileset converges
- Filter variables: Backends provide variables for partition evaluation
"""

from __future__ import annotations
from typing import Protocol, Any
from abc import ABC, abstractmethod
from pathlib import Path

from gbs.tasks import BuildContext, BuildFileSet
from gbs.logging import get_logger


class Backend(Protocol):
    """Protocol for backends that transform the BuildFileSet

    All backends must implement:
    - name: Unique identifier
    - priority: Execution order (lower = earlier, default range 100-999)
    - get_filter_variables(): Provide variables for partition filtering
    - process(): Transform the fileset
    """

    name: str
    priority: int

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables for partition evaluation

        These variables are used when evaluating the source model to determine
        which files should be included in the build.

        Args:
            context: Build context

        Returns:
            Dictionary of variable_name -> value

        Example:
            return {"target_language": "vhdl", "has_verilog_support": True}
        """
        ...

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Process the fileset, transforming it in place

        Backends can:
        - Add new generated files (e.g., transpiled outputs)
        - Remove processed files (e.g., inputs that were transformed)
        - Replace files (e.g., optimized versions)
        - Create build tasks for files
        - Query and filter existing files

        The fileset is modified in place. The modification serial will be
        used to detect convergence.

        Args:
            context: Build context
            fileset: BuildFileSet to transform

        Note:
            This is an async method to support task creation and other
            async operations.
        """
        ...


class BaseBackend(ABC):
    """Base class for backends

    Provides common functionality and enforces the Backend protocol.
    Subclasses must implement get_filter_variables() and process().
    """

    def __init__(self, name: str, priority: int = 500):
        """Initialize backend

        Args:
            name: Unique backend name
            priority: Execution priority (lower = earlier)
                     Suggested ranges:
                     100-299: Preprocessing (transpilers, code generators)
                     300-499: Intermediate processing
                     500-699: Main compilation
                     700-999: Post-processing
        """
        self.name = name
        self.priority = priority
        self.logger = get_logger(f"Backend({name})")

    @abstractmethod
    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables for partition evaluation

        Must be implemented by subclasses.
        """
        ...

    @abstractmethod
    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Process the fileset

        Must be implemented by subclasses.
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name}, priority={self.priority})"


class BackendRegistry:
    """Registry for managing backends

    Maintains the list of registered backends and provides methods for:
    - Registering backends
    - Getting backends in priority order
    - Collecting filter variables from all backends
    """

    def __init__(self):
        """Initialize empty registry"""
        self._backends: list[Backend] = []
        self.logger = get_logger("BackendRegistry")

    def register(self, backend: Backend) -> None:
        """Register a backend

        Args:
            backend: Backend to register

        Raises:
            ValueError: If backend with same name already registered
        """
        # Check for duplicate names
        if any(b.name == backend.name for b in self._backends):
            raise ValueError(f"Backend with name '{backend.name}' already registered")

        self._backends.append(backend)
        self.logger.debug(f"Registered backend: {backend.name} (priority={backend.priority})")

    def get_backends_ordered(self) -> list[Backend]:
        """Get backends in priority order (lowest priority first)

        Returns:
            List of backends sorted by priority
        """
        return sorted(self._backends, key=lambda b: (b.priority, b.name))

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Collect filter variables from all backends

        Args:
            context: Build context

        Returns:
            Combined dictionary of all filter variables

        Note:
            If multiple backends provide the same variable, later backends
            (higher priority) will override earlier ones.
        """
        variables = {}
        for backend in self.get_backends_ordered():
            backend_vars = backend.get_filter_variables(context)
            if backend_vars:
                variables.update(backend_vars)
                self.logger.debug(
                    f"Backend {backend.name} provided variables: {list(backend_vars.keys())}"
                )
        return variables

    def __len__(self) -> int:
        """Number of registered backends"""
        return len(self._backends)

    def __iter__(self):
        """Iterate over backends in priority order"""
        return iter(self.get_backends_ordered())


async def run_backend_iteration(
    context: BuildContext,
    fileset: BuildFileSet,
    registry: BackendRegistry,
    max_iterations: int = 100
) -> int:
    """Run backend iteration loop until convergence

    Iteratively runs all backends until the fileset stops changing
    (modification serial stabilizes).

    Args:
        context: Build context
        fileset: BuildFileSet to process
        registry: Backend registry
        max_iterations: Maximum iterations before giving up

    Returns:
        Number of iterations performed

    Raises:
        RuntimeError: If max_iterations exceeded without convergence

    Example:
        registry = BackendRegistry()
        registry.register(VerilogToVHDL())
        registry.register(GHDL())

        fileset = BuildFileSet(context)
        # ... populate fileset with source files ...

        iterations = await run_backend_iteration(context, fileset, registry)
        print(f"Converged after {iterations} iterations")
    """
    logger = get_logger("BackendIteration")
    iteration = 0

    logger.info(f"Starting backend iteration with {len(registry)} backends")

    while iteration < max_iterations:
        iteration += 1
        serial_before = fileset.modification_serial

        logger.debug(f"Iteration {iteration}: serial={serial_before}, files={len(fileset)}")

        # Run all backends in priority order
        for backend in registry:
            logger.debug(f"Running backend: {backend.name}")
            await backend.process(context, fileset)

        serial_after = fileset.modification_serial

        # Check for convergence
        if serial_after == serial_before:
            logger.info(
                f"Converged after {iteration} iterations "
                f"(serial={serial_after}, files={len(fileset)})"
            )
            return iteration

        logger.debug(
            f"Iteration {iteration} complete: "
            f"serial {serial_before} -> {serial_after}, "
            f"files={len(fileset)}"
        )

    # Failed to converge
    raise RuntimeError(
        f"Backend iteration did not converge after {max_iterations} iterations. "
        f"This may indicate a backend is continuously modifying the fileset."
    )


# Example backend implementations for reference

class VerilogToVHDLBackend(BaseBackend):
    """Example backend that transpiles Verilog to VHDL

    This is a reference implementation demonstrating how a transpiler backend works:
    - Finds Verilog files in the fileset
    - Creates VHDL equivalents (simulated - doesn't actually transpile)
    - Removes Verilog files
    - Adds generated VHDL files

    Priority: 200 (preprocessing/transpilation)
    """

    def __init__(self):
        super().__init__("verilog_to_vhdl", priority=200)
        self._processed_files: set[Path] = set()

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables indicating VHDL is the target"""
        return {
            "target_language": "vhdl",
            "has_verilog_transpiler": True,
        }

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Transpile all Verilog files to VHDL"""
        from gbs.tasks import BuildResource

        # Find all Verilog files we haven't processed yet
        verilog_files = [
            br for br in fileset.filter(file_type="verilog")
            if br.path not in self._processed_files
        ]

        if not verilog_files:
            return  # Nothing to do

        self.logger.info(f"Transpiling {len(verilog_files)} Verilog files to VHDL")

        for verilog_br in verilog_files:
            # Generate VHDL file path
            vhdl_path = verilog_br.path.with_suffix(".vhd")

            # Create VHDL BuildResource
            vhdl_br = BuildResource(
                resource=context.get_resource(vhdl_path),
                file_type="vhdl",
                library=verilog_br.library,
                language_version="2008",
                is_source=False,
                generated_by=self.name,
            )

            # Copy dependencies
            vhdl_br.depends_on = verilog_br.depends_on.copy()

            # Replace Verilog with VHDL
            fileset.replace(verilog_br.path, vhdl_br, transfer_dependencies=True)

            # Mark as processed
            self._processed_files.add(verilog_br.path)

            self.logger.debug(f"Transpiled {verilog_br.path.name} -> {vhdl_path.name}")


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
        topcell: str | None = None
    ):
        super().__init__("ghdl", priority=500)
        self.output_dir = Path(output_dir) if output_dir is not None else Path("build")
        self.vhdl_std = vhdl_std
        self.topcell = topcell
        self._ghdl_backend_type: str | None = None
        self._processed_libraries: set[str] = set()

    def _detect_ghdl_backend(self) -> str:
        """Detect GHDL backend type (mcode, gcc, or llvm)

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
                ["ghdl", "--version"],
                capture_output=True,
                text=True,
                check=True
            )

            # Look for "code generator" line
            # Format can be: " llvm 19.1.7 code generator" or "code generator: mcode"
            for line in result.stdout.split('\n'):
                if 'code generator' in line.lower():
                    line_lower = line.lower()
                    # Check all words in the line for known backends
                    words = line_lower.split()
                    for word in words:
                        if word in ['mcode', 'gcc', 'llvm']:
                            self._ghdl_backend_type = word
                            self.logger.info(f"Detected GHDL backend: {word}")
                            return word

            raise RuntimeError("Could not detect GHDL backend type from --version output")

        except FileNotFoundError:
            raise RuntimeError("ghdl not found in PATH")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ghdl --version failed: {e}")

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables for GHDL"""
        return {
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
        from gbs.tasks import BuildResource, ExecutorTask

        # Detect GHDL backend type
        backend_type = self._detect_ghdl_backend()

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Get VHDL version suffix for .cf files
        vhdl_version = "93" if "93" in self.vhdl_std else "08"

        # Get libraries in dependency order
        by_library = fileset.by_library_ordered()

        # Debug: check file order before processing
        for lib_name, lib_files in by_library:
            if lib_name and lib_name == "nsl_simulation":
                vhdl_only = [br for br in lib_files if br.file_type == "vhdl"]
                self.logger.info(f"DEBUG: by_library_ordered for {lib_name}: {[br.path.name for br in vhdl_only]}")

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
            # Since by_library_ordered() returns libs in dependency order,
            # we can reference all previously processed libraries
            p_flags = []
            for dep_lib in cf_files.keys():
                if dep_lib != library_name:  # Don't add self
                    dep_workdir = self.output_dir / dep_lib
                    p_flags.append(f"-P{dep_workdir.resolve()}")
            self.logger.debug(f"Library {library_name} p_flags: {p_flags}")

            # Create import task (ghdl -i)
            # Capture loop variables with default arguments
            def make_import_executor(wd, std, lib, pflags, srcs, cfp):
                async def import_executor(ctx, inputs):
                    cmd = [
                        "ghdl", "-i",
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
                workdir, self.vhdl_std, library_name, p_flags[:], source_paths[:], cf_path
            )

            # Import depends on source files and dependent library .cf files
            # Since by_library_ordered() returns libraries in dependency order,
            # we can depend on all previously processed libraries
            import_inputs = [br.resource for br in vhdl_files]
            for dep_lib in cf_files.keys():
                if dep_lib != library_name:  # Don't depend on self
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
            def make_analyze_executor(wd, std, lib, pflags, srcs, cfp, objfiles):
                async def analyze_executor(ctx, inputs):
                    cmd = [
                        "ghdl", "-a",
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
                workdir, self.vhdl_std, library_name, p_flags[:], source_paths[:], cf_path, object_files[:]
            )

            # Analyze inputs: source files and dependent library analyze tasks
            # Since by_library_ordered() returns libraries in dependency order,
            # we can depend on all previously processed libraries' analyze tasks
            analyze_inputs = [import_task] + [br.resource for br in vhdl_files]
            for dep_lib in analyze_tasks.keys():
                if dep_lib != library_name:  # Don't depend on self
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
        if self.topcell and cf_files:
            # Find the root library (last one in dependency order)
            root_library = by_library[-1][0] if by_library else None

            if root_library:
                self._create_elaboration_tasks(
                    context,
                    fileset,
                    backend_type,
                    root_library,
                    cf_files,
                    vhdl_version
                )

    def _create_elaboration_tasks(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
        backend_type: str,
        root_library: str,
        cf_files: dict[str, BuildResource],
        vhdl_version: str
    ):
        """Create elaboration/linking tasks for the top entity"""
        from gbs.tasks import BuildResource, ExecutorTask
        import subprocess

        root_workdir = self.output_dir / root_library

        # Build -P flags for all libraries
        p_flags = []
        for lib_name in cf_files.keys():
            lib_workdir = self.output_dir / lib_name
            p_flags.append(f"-P{lib_workdir.resolve()}")

        if backend_type in ["gcc", "llvm"]:
            # Compiled backend: ghdl -c -e
            executable_path = Path.cwd() / self.topcell
            executable_resource = context.get_resource(executable_path)

            async def compile_link_executor(ctx, inputs):
                cmd = [
                    "ghdl", "-c", "-O2",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={self.vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    "-e", self.topcell
                ]

                self.logger.info(f"Linking: {' '.join(cmd)}")

                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"ghdl -c -e failed: {result.stderr}")

                return [executable_path]

            link_task = ExecutorTask(
                context,
                f"ghdl_link_{self.topcell}",
                inputs=[cf_files[lib].resource for lib in cf_files],
                outputs=[executable_resource],
                executor=compile_link_executor,
                description=f"GHDL link {self.topcell}"
            )

            sim_br = BuildResource(
                resource=executable_resource,
                file_type="ghdl_simulator",
                library=root_library,
                is_source=False,
                generated_by=self.name,
            )
            fileset.add(sim_br)

        else:  # mcode
            # Mcode backend: ghdl -m, ghdl -e, then create run script

            # ghdl -m (make)
            async def make_executor(ctx, inputs):
                cmd = [
                    "ghdl", "-m",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={self.vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    self.topcell
                ]

                self.logger.info(f"Make: {' '.join(cmd)}")

                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"ghdl -m failed: {result.stderr}")

                # Return a marker file
                marker = root_workdir / f".{self.topcell}_made"
                marker.touch()
                return [marker]

            make_marker = context.get_resource(root_workdir / f".{self.topcell}_made")

            make_task = ExecutorTask(
                context,
                f"ghdl_make_{self.topcell}",
                inputs=[cf_files[lib].resource for lib in cf_files],
                outputs=[make_marker],
                executor=make_executor,
                description=f"GHDL make {self.topcell}"
            )

            # ghdl -e (elaborate)
            async def elab_executor(ctx, inputs):
                cmd = [
                    "ghdl", "-e",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={self.vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    self.topcell
                ]

                self.logger.info(f"Elaborate: {' '.join(cmd)}")

                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"ghdl -e failed: {result.stderr}")

                # Create run script
                script_path = Path.cwd() / self.topcell
                run_cmd = [
                    "ghdl", "-r",
                    f"--workdir={root_workdir.resolve()}",
                    f"--std={self.vhdl_std}",
                ] + p_flags + [
                    f"--work={root_library}",
                    self.topcell,
                    '"$@"'
                ]

                script_content = f"""#!/bin/sh
{' '.join(run_cmd)}
"""
                script_path.write_text(script_content)
                script_path.chmod(0o755)

                return [script_path]

            script_resource = context.get_resource(Path.cwd() / self.topcell)

            elab_task = ExecutorTask(
                context,
                f"ghdl_elab_{self.topcell}",
                inputs=[make_marker],
                outputs=[script_resource],
                executor=elab_executor,
                description=f"GHDL elaborate {self.topcell}"
            )

            sim_br = BuildResource(
                resource=script_resource,
                file_type="ghdl_simulator",
                library=root_library,
                is_source=False,
                generated_by=self.name,
            )
            fileset.add(sim_br)


class MemInitBackend(BaseBackend):
    """Example backend that generates memory initialization files

    This demonstrates a code generation backend:
    - Finds memory specification files
    - Generates initialization files
    - Runs only once (idempotent)

    Priority: 150 (preprocessing/code generation)
    """

    def __init__(self):
        super().__init__("mem_init", priority=150)
        self._generated = False

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables"""
        return {
            "has_mem_init": True,
        }

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Generate memory initialization files"""
        from gbs.tasks import BuildResource

        if self._generated:
            return  # Only run once

        # Find memory spec files
        mem_specs = fileset.filter(file_type="mem_spec")

        if not mem_specs:
            self._generated = True
            return

        self.logger.info(f"Generating {len(mem_specs)} memory init files")

        for spec_br in mem_specs:
            # Generate VHDL initialization package
            init_path = spec_br.path.with_name(spec_br.path.stem + "_init.vhd")

            init_br = BuildResource(
                resource=context.get_resource(init_path),
                file_type="vhdl",
                library=spec_br.library,
                is_source=False,
                generated_by=self.name,
            )
            init_br.depends_on.add(spec_br)

            fileset.add(init_br)

            self.logger.debug(f"Generated {init_path.name} from {spec_br.path.name}")

        self._generated = True
