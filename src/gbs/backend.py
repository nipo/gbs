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
    """Example backend that compiles VHDL using GHDL

    This is a reference implementation demonstrating how a compiler backend works:
    - Finds VHDL files to compile
    - Creates compilation tasks in dependency order
    - Generates elaborated outputs

    Priority: 500 (main compilation)
    """

    def __init__(self, output_dir: Path | None = None):
        super().__init__("ghdl", priority=500)
        self.output_dir = output_dir
        self._compiled_files: set[Path] = set()

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
        """Compile all VHDL files with GHDL"""
        from gbs.tasks import BuildResource, ExecutorTask

        # Find all VHDL files we haven't compiled yet
        vhdl_files = [
            br for br in fileset.filter(file_type="vhdl")
            if br.path not in self._compiled_files
        ]

        if not vhdl_files:
            return  # Nothing to do

        self.logger.info(f"Compiling {len(vhdl_files)} VHDL files with GHDL")

        # Get libraries in dependency order
        by_library = fileset.by_library_ordered()

        for library_name, library_files in by_library:
            if library_name is None:
                continue  # Skip files with no library

            self.logger.debug(f"Compiling library {library_name} ({len(library_files)} files)")

            for vhdl_br in library_files:
                if vhdl_br.path in self._compiled_files:
                    continue

                # Create elaborated output
                if self.output_dir:
                    elab_path = self.output_dir / f"{vhdl_br.path.stem}_elab.o"
                else:
                    elab_path = vhdl_br.path.with_suffix(".o")

                elab_br = BuildResource(
                    resource=context.get_resource(elab_path),
                    file_type="vhdl_elab",
                    library=library_name,
                    is_source=False,
                    generated_by=self.name,
                )

                # Elaborated file depends on source
                elab_br.depends_on.add(vhdl_br)

                # Create compilation task (simulated)
                async def compile_executor(ctx, inputs):
                    # Simulate compilation
                    self.logger.debug(f"Compiling {vhdl_br.path.name} -> {elab_path.name}")
                    # In real implementation, would run: ghdl -a --std=08 file.vhd
                    return [elab_path]

                task = ExecutorTask(
                    context,
                    f"ghdl_compile_{vhdl_br.path.name}",
                    inputs=[vhdl_br.resource],
                    outputs=[elab_br.resource],
                    executor=compile_executor,
                    description=f"GHDL compile {vhdl_br.path.name}"
                )

                # Add elaborated file to fileset
                fileset.add(elab_br)

                # Mark as compiled
                self._compiled_files.add(vhdl_br.path)


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
