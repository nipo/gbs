"""GBS Core Data Models

Data structures representing the GBS build system components.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SourceFile:
    """Represents a source file in the project

    Attributes:
        path: Path to the source file (relative to partition root)
        file_type: File type string (e.g., "vhdl", "verilog", "gowin-cst", etc.)
        variant: Optional type variant (e.g., "2008" for VHDL-2008)
    """
    path: Path
    file_type: str
    variant: Optional[str] = None

    def __str__(self) -> str:
        if self.variant:
            return f"{self.path} ({self.file_type}-{self.variant})"
        return f"{self.path} ({self.file_type})"


@dataclass
class Partition:
    """An enumerated partition within a library

    This is an expanded view of a repository partition when filter vars
    have been applied. All conditional logic has been resolved.

    Attributes:
        name: Partition name (format: "library.partition")
        sources: List of source files
        deps: Set of dependencies to other partitions (format: "library.partition")
    """
    name: str
    sources: list[SourceFile] = field(default_factory=list)
    deps: set[str] = field(default_factory=set)

    def __str__(self) -> str:
        return f"Partition({self.name}, {len(self.sources)} sources, {len(self.deps)} deps)"


class Repository(ABC):
    """A repository containing multiple libraries and partitions

    Instances of this ABC are repository enumeration plugins,
    instantiated once per declared repository.

    Attributes:
        name: Repository name
        root: Root path of the repository
    """

    def __init__(self, name: str, root: Path):
        """Initialize repository

        Args:
            name: Repository name
            root: Root path of the repository
        """
        self.name = name
        self.root = root
        self.definition_files: list[Path] = []  # Files read during loading

    @abstractmethod
    def file_types(self) -> set[str]:
        """Get set of all file types possibly provided by this repository

        This is used by the build planner to determine what file types
        are available as sources. Implementation may return more file
        types than actually selected for a build.

        Returns:
            Set of file type strings (e.g., {"vhdl", "verilog", "systemverilog"})
        """
        pass

    @abstractmethod
    def partition_lookup(self, partition_name: str, filter_vars: dict[str, str]) -> Optional[Partition]:
        """Get a partition by name with filter variables applied

        Partition name is in "library.partition" format. The repository
        enumerates and filters the partition based on filter_vars, returning
        an expanded Partition with resolved sources and dependencies.

        Args:
            partition_name: Partition name in "library.partition" format
            filter_vars: Filter variables (e.g., {"target": "simulation", "tool": "ghdl"})

        Returns:
            Expanded Partition object if found, None otherwise
        """
        pass

    def __str__(self) -> str:
        return f"Repository({self.name})"


@dataclass
class SourceFileSet:
    """Ordered source file set after dependency resolution

    This represents the final, resolved set of source files to build after
    dependency traversal and filtering.

    Attributes:
        partitions: Ordered list of partition names (in dependency order)
        sources: Dictionary mapping partition name to source files
        partition_deps: Dictionary mapping partition name to set of dependency partition names
    """
    partitions: list[str] = field(default_factory=list)
    sources: dict[str, list[SourceFile]] = field(default_factory=dict)
    partition_deps: dict[str, set[str]] = field(default_factory=dict)

    def __str__(self) -> str:
        total_files = sum(len(f) for f in self.sources.values())
        return f"SourceFileSet({len(self.partitions)} partitions, {total_files} files)"

    def add_partition(self, partition: Partition):
        """Add a partition to the build set

        Args:
            partition: Partition to add
        """
        if partition.name not in self.partitions:
            self.partitions.append(partition.name)

        self.sources[partition.name] = partition.sources
        self.partition_deps[partition.name] = partition.deps

    def get_all_files(self) -> list[SourceFile]:
        """Get all source files in build order"""
        result = []
        for partition_name in self.partitions:
            result.extend(self.sources.get(partition_name, []))
        return result
