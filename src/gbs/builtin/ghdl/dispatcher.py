"""GHDL Dispatchers - VHDL analysis and simulation"""

from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
import sys
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

        # Content hash cache for source files: keyed by (resolved path, mtime
        # in ns, size). Source files are immutable during a build, so hashing
        # any given file once is correct.
        self.__content_hash_cache: dict[tuple, str] = {}

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
            ghdl_executable = self.get_tool_option("executable", "ghdl")
            # Expand ~ and environment variables in executable path
            ghdl_executable = str(expand_path(ghdl_executable))
            logger.debug(f"Using GHDL executable: {ghdl_executable}")

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

    def library_cache_workdir(self, library: str, signature: str) -> Path:
        """Content-addressed shared workdir for a library's analyzed form.

        The path encodes the library name, vhdl standard, GHDL backend, and
        the signature that summarises all inputs that affect what GHDL writes
        here. Two BuildContexts sharing the same ResourceRegistry will
        produce the same path for the same inputs, which deduplicates the
        Import task through the shared Resource at this location.
        """
        _, backend = self._get_ghdl_config()
        slot = f"{self.ghdl_vhdl_version.rstrip('c')}-{backend}"
        return self.context.shared_cache_root / "ghdl" / slot / f"{library}-{signature}"

    def library_elaboration_workdir(self, library: str) -> Path:
        """Per-project workdir used by elaboration commands.

        Elaboration writes outputs that are specific to the project (the
        topcell binary, link artifacts). They must not collide with another
        project's elaboration writes, so this workdir is rooted in the
        per-project output path rather than the shared cache.
        """
        return self.context.output_path / library

    def materialize_library_workdir(
        self,
        source_workdir: Path,
        dest_workdir: Path,
    ) -> None:
        """Populate dest_workdir with hardlinks (or copies) to source_workdir.

        Used to give elaboration its own writeable workdir while reusing the
        analyzed cf and any compiled object files from the shared cache.
        Existing files at dest_workdir are left untouched.
        """
        dest_workdir.mkdir(parents=True, exist_ok=True)
        if not source_workdir.exists():
            return
        for src in source_workdir.iterdir():
            if not src.is_file():
                continue
            dst = dest_workdir / src.name
            if dst.exists():
                continue
            try:
                os.link(src, dst)
            except OSError:
                # Different filesystems, or the platform forbids hardlinks
                # for this combination — copy is correct but slower.
                shutil.copy2(src, dst)

    def content_hash(self, path: Path) -> str:
        """Cached SHA-256 of a file's bytes."""
        resolved = path.resolve()
        try:
            stat = resolved.stat()
        except FileNotFoundError:
            return "missing"
        key = (resolved, stat.st_mtime_ns, stat.st_size)
        cached = self.__content_hash_cache.get(key)
        if cached is not None:
            return cached
        h = hashlib.sha256()
        with open(resolved, "rb") as f:
            while True:
                chunk = f.read(1 << 16)
                if not chunk:
                    break
                h.update(chunk)
        digest = h.hexdigest()
        self.__content_hash_cache[key] = digest
        return digest


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
        # Cached signatures, persisted across dispatch iterations. Once a
        # library's signature is computed, the corresponding cf Resource lives
        # at a fixed cache path and the Import task is registered. We must not
        # change the signature later — a different signature would point at a
        # different cache path and break the dedup with peer dispatchers that
        # share the registry.
        self._library_signatures: dict[str, str] = {}

    def _compute_library_signature(
        self,
        library: str,
        sources: list,
        dep_signatures: dict[str, str],
    ) -> str:
        """Content hash that captures everything affecting analysis output.

        Includes the source list and contents, vhdl standard, GHDL backend
        and version, analyze args, and transitive dependency signatures.
        Two libraries with the same signature produce byte-identical
        analyzed output and can therefore share a workdir on disk.
        """
        _, backend = self._get_ghdl_config()
        analyze_args = list(self.get_tool_option("analyze_args", []))

        material = {
            "library": library,
            "vhdl_std": self.vhdl_std,
            "ghdl_vhdl_version": self.ghdl_vhdl_version,
            "backend": backend,
            "analyze_args": analyze_args,
            "sources": [
                {
                    "path": str(s.path.resolve()),
                    "hash": self.content_hash(s.path),
                }
                for s in sorted(sources, key=lambda r: str(r.path.resolve()))
            ],
            "deps": [
                {"library": d, "signature": dep_signatures[d]}
                for d in sorted(dep_signatures)
            ],
        }
        encoded = json.dumps(material, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]

    def library_build_get(self, library: str, signature: str) -> tuple['Resource', Task]:
        """Get or create library build task and resource

        Args:
            library: Library name
            signature: Content-addressed signature for this library's inputs

        Returns:
            Tuple of (cf resource, import task)
        """
        try:
            return self._library_build[library]
        except KeyError:
            pass

        workdir = self.library_cache_workdir(library, signature)

        # .cf file at the content-addressed cache location
        cf_path = workdir / f"{library}-obj{self.ghdl_vhdl_version.rstrip('c')}.cf"
        cf_resource = self.context.get_resource(
            cf_path,
            file_type="ghdl-cf",
            library=library,
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        # Adopt an existing Import task if one already targets this cf path
        # via the shared ResourceRegistry. Same path implies same signature
        # implies the same inputs, so the registered task is the right one
        # for us too. This is how a second project in the suite avoids
        # recompiling a library that the first project already covered.
        existing = next(
            (d for d in cf_resource.depends_on if isinstance(d, Task)),
            None,
        )
        if existing is not None:
            self._library_build[library] = (cf_resource, existing)
            # The cf was added to its producing context's pending queue when
            # that context created the Import task. For downstream dispatchers
            # in this context (e.g. GHDLSimulateDispatcher building elaboration
            # -P flags via filter_pending) to see it, we must add it to this
            # context's pending queue too.
            self.context.add_pending(cf_resource)
            return cf_resource, existing

        t = task.Import(
            dispatcher=self,
            library_name=library,
            inputs=[],
            outputs=[cf_resource],
        )

        self._library_build[library] = cf_resource, t

        return cf_resource, t

    async def process(self) -> None:
        """Analyze VHDL sources into library intermediates"""
        ordered_libs = self.context._pending_libraries_in_dependency_order()
        transitive_deps_graph = self.context._pending_library_dependency_graph_transitive()

        # Skip work if every library currently in the pending graph already
        # has a signature assigned. After iteration 1 the VHDL sources have
        # been consumed, but cf resources are still in pending: that creates
        # a spurious second visit with empty sources, which would otherwise
        # compute a different signature for already-processed libraries.
        unprocessed = [l for l in ordered_libs if l not in self._library_signatures]
        if not unprocessed and self._library_signatures:
            return

        # Per-library VHDL sources currently in pending
        lib_sources: dict[str, list] = {}
        for library_name, library_files in self.context.get_pending_by_library_ordered():
            if library_name is None:
                continue
            lib_sources[library_name] = [
                r for r in library_files if r.file_type == "vhdl"
            ]

        # Compute signatures in topological order so each library's signature
        # can incorporate its transitive deps' signatures.
        for lib in ordered_libs:
            if lib in self._library_signatures:
                continue
            sources = lib_sources.get(lib, [])
            transitive = transitive_deps_graph.get(lib, set())
            missing = transitive - set(self._library_signatures)
            if missing:
                raise RuntimeError(
                    f"GHDL: dependencies of '{lib}' lack signatures: {missing}"
                )
            dep_sigs = {d: self._library_signatures[d] for d in transitive}
            self._library_signatures[lib] = self._compute_library_signature(
                lib, sources, dep_sigs,
            )

        # Create or adopt cf resources and tasks, then wire up dep cf inputs
        for lib in unprocessed:
            sig = self._library_signatures[lib]
            cf_resource, task_obj = self.library_build_get(lib, sig)

            for d in transitive_deps_graph.get(lib, set()):
                dep_sig = self._library_signatures[d]
                dep_cf, _ = self.library_build_get(d, dep_sig)
                task_obj.add_input(dep_cf, consume=False)

        # Wire VHDL source inputs. add_input is idempotent on adopted tasks,
        # so a task created by a peer dispatcher tolerates being called again
        # with the same source resources from this context's pending queue.
        for lib in unprocessed:
            _, task_obj = self._library_build[lib]
            for resource in lib_sources.get(lib, []):
                task_obj.add_input(resource)


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
            # These are INTERMEDIATE resources - don't consume them (consume=False)
            # so they remain available for other tasks
            self._linker.add_input(resource, consume=False)

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
            executable_name = "simulator.exe"
        else:
            final_task_class = task.MakeElab
            executable_name = "simulator.bat" if sys.platform == "win32" else "simulator.exe"

        executable_path = self.context.output_path / executable_name
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

            log_resource = self.context.get_resource(
                self.context.output_path / "simulation_run" / "simulation.log",
                file_type="simulation-log",
                typology=ResourceTypology.OUTPUT,
                generated_by=self.name,
            )

            self._run_task = task.RunSimulation(
                dispatcher=self,
                inputs=[simulator],
                outputs=[success_resource, log_resource]
            )

        if not self._run_task.outputs_of_type(["waveform-vcd", "waveform-ghw", "waveform-fst"]):
            for waveform in list(self.context.filter_pending(file_type=["waveform-vcd", "waveform-ghw", "waveform-fst"])):
                if waveform.depends_on:
                    continue

                self._run_task.add_output(waveform)
                break
