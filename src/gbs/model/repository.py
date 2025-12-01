"""GBS Core Data Models

Data structures representing the GBS build system components.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any


@dataclass
class SourceFile:
    """Represents a source file in the project

    Attributes:
        path: Path to the source file (relative to partition root)
        language: File type/language string (e.g., "vhdl", "verilog", "gowin-cst", etc.)
        variant: Optional language variant (e.g., "2008" for VHDL-2008)
    """
    path: Path
    language: str
    variant: Optional[str] = None

    def __str__(self) -> str:
        if self.variant:
            return f"{self.path} ({self.language}-{self.variant})"
        return f"{self.path} ({self.language})"


@dataclass
class FilterCondition:
    """A single conditional branch within a group

    Represents one branch of a conditional selection. Conditions are
    evaluated in order within a group (first-match wins). A condition
    can specify dependencies, sources, and nested conditional groups.

    Attributes:
        expression: Filter expression (e.g., "vendor = \"xilinx\"" or "default")
        deps: List of partition dependencies (format: "library.partition")
        sources: List of source files
        groups: Nested conditional groups (for hierarchical conditions)
    """
    expression: str
    deps: list[str] = field(default_factory=list)
    sources: list[SourceFile] = field(default_factory=list)
    groups: list['ConditionalGroup'] = field(default_factory=list)

    def is_default(self) -> bool:
        """Check if this is a default (catch-all) condition"""
        return self.expression.strip() == "default"


@dataclass
class ConditionalGroup:
    """A group of mutually exclusive conditions

    Conditions within a group are evaluated in order. The first matching
    condition wins, and no further conditions are evaluated. This implements
    a switch/case-like semantic.

    Attributes:
        name: Group name (for debugging and documentation)
        conditions: List of conditions (evaluated in order, first-match wins)
    """
    name: str
    conditions: list[FilterCondition] = field(default_factory=list)

    def __str__(self) -> str:
        return f"ConditionalGroup({self.name}, {len(self.conditions)} conditions)"


@dataclass
class Partition:
    """A partition within a library

    Partitions group related source files and can depend on other partitions.
    They contain conditional groups that define sources and dependencies
    based on filter variables.

    Note: Partitions have no direct YAML representation. The partition name
    is derived from the filename, and the YAML content is parsed as a
    FilterCondition (the root of the partition).

    Attributes:
        name: Partition name (derived from filename)
        groups: List of conditional groups (all groups are evaluated)
    """
    name: str
    groups: list[ConditionalGroup] = field(default_factory=list)

    def __str__(self) -> str:
        return f"Partition({self.name}, {len(self.groups)} groups)"


@dataclass
class Library:
    """A library containing multiple partitions

    Libraries provide symbol name scoping and group related partitions.

    Attributes:
        name: Library name
        partitions: Dictionary of partition name -> Partition
        description: Optional description
    """
    name: str
    partitions: dict[str, Partition] = field(default_factory=dict)
    description: Optional[str] = None

    def __str__(self) -> str:
        return f"Library({self.name}, {len(self.partitions)} partitions)"

    def add_partition(self, partition: Partition):
        """Add a partition to this library"""
        self.partitions[partition.name] = partition

    def get_partition(self, name: str) -> Optional[Partition]:
        """Get a partition by name"""
        return self.partitions.get(name)


@dataclass
class Repository:
    """A repository containing multiple libraries

    Repositories group related libraries as a coherent set of functionality.

    Attributes:
        name: Repository name
        root: Root path of the repository
        libraries: Dictionary of library name -> Library
        description: Optional description
    """
    name: str
    root: Path
    libraries: dict[str, Library] = field(default_factory=dict)
    description: Optional[str] = None

    def __str__(self) -> str:
        return f"Repository({self.name}, {len(self.libraries)} libraries)"

    def add_library(self, library: Library):
        """Add a library to this repository"""
        self.libraries[library.name] = library

    def get_library(self, name: str) -> Optional[Library]:
        """Get a library by name"""
        return self.libraries.get(name)


@dataclass
class Project:
    """A gateware project definition

    Projects define a single root partition and build configuration.
    The root partition is always placed in the "work" library (required by synthesis tools).

    Attributes:
        name: Project name
        root_partition: The project's root partition (in "work" library)
        topcell: Top-level entity/module name (deprecated, use output_groups)
        filter_vars: Variables used for filter evaluation (deprecated, use output_groups)
        description: Optional description
        raw_config: Raw configuration dictionary (for accessing backends etc)
        output_groups: List of output groups for new pass-based build planning

    Note:
        The topcell and filter_vars fields are deprecated in favor of the new
        output_groups field. Both are kept for backward compatibility during
        the transition to the new pass-based architecture.
    """
    name: str
    root_partition: Partition
    topcell: str
    filter_vars: dict[str, str | int] = field(default_factory=dict)
    description: Optional[str] = None
    raw_config: dict = field(default_factory=dict)
    output_groups: list['OutputGroup'] = field(default_factory=list)

    @property
    def root_library_name(self) -> str:
        """The root library is always 'work' for synthesis tools"""
        return "work"

    def __str__(self) -> str:
        return f"Project({self.name}, topcell={self.topcell})"


@dataclass
class SourceFileSet:
    """Ordered source file set after dependency resolution

    This represents the final, resolved set of source files to build after
    dependency traversal and filtering.

    Attributes:
        libraries: Ordered list of libraries (in dependency order)
        partitions: Dictionary mapping library name to ordered partition list
        files: Dictionary mapping (library, partition) to source files
        partition_deps: Dictionary mapping (library, partition) to set of (lib, part) dependencies
    """
    libraries: list[str] = field(default_factory=list)
    partitions: dict[str, list[str]] = field(default_factory=dict)
    files: dict[tuple[str, str], list[SourceFile]] = field(default_factory=dict)
    partition_deps: dict[tuple[str, str], set[tuple[str, str]]] = field(default_factory=dict)

    def __str__(self) -> str:
        total_files = sum(len(f) for f in self.files.values())
        return f"SourceFileSet({len(self.libraries)} libraries, {total_files} files)"

    def add_partition(self, library: str, partition: str, files: list[SourceFile], deps: list[tuple[str, str]] = None):
        """Add a partition with its files to the build set

        Args:
            library: Library name
            partition: Partition name
            files: Source files in the partition
            deps: List of (library, partition) tuples this partition depends on
        """
        if library not in self.partitions:
            self.libraries.append(library)
            self.partitions[library] = []

        if partition not in self.partitions[library]:
            self.partitions[library].append(partition)

        self.files[(library, partition)] = files

        # Store partition dependencies
        if deps is not None:
            self.partition_deps[(library, partition)] = set(deps)
        else:
            self.partition_deps[(library, partition)] = set()

    def get_all_files(self) -> list[SourceFile]:
        """Get all source files in build order"""
        result = []
        for library in self.libraries:
            for partition in self.partitions.get(library, []):
                result.extend(self.files.get((library, partition), []))
        return result


@dataclass
class OutputFile:
    """Desired output file for a build

    Represents one output file that should be produced by the build.
    The build planner will find a path from sources to this output type.

    Attributes:
        type: Output file type (e.g., "simulator", "gowin-fs", "verilog")
        path: Desired output path

    Examples:
        >>> OutputFile(type="simulator", path=Path("build/sim.exe"))
        >>> OutputFile(type="gowin-fs", path=Path("build/bitstream.fs"))
    """
    type: str
    path: Path

    def __str__(self) -> str:
        return f"OutputFile({self.type}, {self.path})"


@dataclass
class OutputGroup:
    """A coherent build output with its own topcell and configuration

    Output groups define what to build. Each output group has its own:
    - topcell (entry point)
    - filter_vars (for source selection)
    - backend_config (backend-specific settings)
    - desired output files
    - optional constraints on which passes/backends to use

    The build planner creates a BuildPlan for each OutputGroup by finding
    paths from sources to the desired outputs.

    Attributes:
        name: Output group name (for identification)
        topcell: Top-level entity/module name
        filter_vars: Variables used for filter evaluation during source enumeration
        backend_config: Dict mapping backend module names to their config
        outputs: List of desired output files
        require_passes: Passes that MUST be in the build plan
        exclude_passes: Passes that MUST NOT be in the build plan
        require_backends: Backends that MUST contribute to the build plan
        exclude_backends: Backends that MUST NOT contribute to the build plan

    Examples:
        >>> # Simulation output group
        >>> OutputGroup(
        ...     name="simulation",
        ...     topcell="testbench",
        ...     filter_vars={"sim": 1, "vendor": "generic"},
        ...     backend_config={"gbs.backend.ghdl": {"vhdl_standard": "2008"}},
        ...     outputs=[OutputFile(type="simulator", path=Path("sim.exe"))]
        ... )
        >>>
        >>> # Synthesis output group with constraints
        >>> OutputGroup(
        ...     name="gowin_synth",
        ...     topcell="top",
        ...     filter_vars={"vendor": "gowin"},
        ...     require_backends=["gbs.backend.gowin"],
        ...     outputs=[
        ...         OutputFile(type="gowin-fs", path=Path("bitstream.fs")),
        ...         OutputFile(type="gowin-bin", path=Path("flash.bin"))
        ...     ]
        ... )
    """
    name: str
    topcell: str
    filter_vars: dict[str, Any] = field(default_factory=dict)
    backend_config: dict[str, dict] = field(default_factory=dict)
    outputs: list[OutputFile] = field(default_factory=list)

    # Build planning constraints
    require_passes: list[str] = field(default_factory=list)
    exclude_passes: list[str] = field(default_factory=list)
    require_backends: list[str] = field(default_factory=list)
    exclude_backends: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"OutputGroup({self.name}, topcell={self.topcell}, {len(self.outputs)} outputs)"
