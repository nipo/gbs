"""GBS Core Data Models

Data structures representing the GBS build system components.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from enum import Enum


class Language(str, Enum):
    """Supported HDL languages"""
    VHDL = "vhdl"
    VERILOG = "verilog"
    SYSTEMVERILOG = "systemverilog"
    CHISEL = "chisel"
    OTHER = "other"


@dataclass
class SourceFile:
    """Represents a source file in the project

    Attributes:
        path: Path to the source file (relative to partition root)
        language: Programming language of the file
        variant: Optional language variant (e.g., "2008" for VHDL-2008)
    """
    path: Path
    language: Language
    variant: Optional[str] = None

    def __str__(self) -> str:
        if self.variant:
            return f"{self.path} ({self.language.value}-{self.variant})"
        return f"{self.path} ({self.language.value})"


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
class ToolsuiteConfig:
    """Configuration for a build toolsuite

    Attributes:
        name: Toolsuite name (e.g., "vivado", "quartus")
        backend: Backend package to use (e.g., "gbs.backends.vivado")
        config: Toolsuite-specific configuration dictionary
    """
    name: str
    backend: str
    config: dict = field(default_factory=dict)


@dataclass
class Project:
    """A gateware project definition

    Projects define the root library, target toolsuite, and build configuration.

    Attributes:
        name: Project name
        root_library: The project's root library
        toolsuite: Toolsuite configuration
        topcell: Entry point entity/module name
        output_format: Desired output format (e.g., "bitstream", "netlist")
        filter_vars: Variables used for filter evaluation
        description: Optional description
    """
    name: str
    root_library: Library
    toolsuite: ToolsuiteConfig
    topcell: str
    output_format: str
    filter_vars: dict[str, str | int] = field(default_factory=dict)
    description: Optional[str] = None

    def __str__(self) -> str:
        return f"Project({self.name}, topcell={self.topcell})"


@dataclass
class BuildFileSet:
    """Ordered build file set after dependency resolution

    This represents the final, resolved set of files to build after
    dependency traversal and filtering.

    Attributes:
        libraries: Ordered list of libraries (in dependency order)
        partitions: Dictionary mapping library name to ordered partition list
        files: Dictionary mapping (library, partition) to source files
    """
    libraries: list[str] = field(default_factory=list)
    partitions: dict[str, list[str]] = field(default_factory=dict)
    files: dict[tuple[str, str], list[SourceFile]] = field(default_factory=dict)

    def __str__(self) -> str:
        total_files = sum(len(f) for f in self.files.values())
        return f"BuildFileSet({len(self.libraries)} libraries, {total_files} files)"

    def add_partition(self, library: str, partition: str, files: list[SourceFile]):
        """Add a partition with its files to the build set"""
        if library not in self.partitions:
            self.libraries.append(library)
            self.partitions[library] = []

        if partition not in self.partitions[library]:
            self.partitions[library].append(partition)

        self.files[(library, partition)] = files

    def get_all_files(self) -> list[SourceFile]:
        """Get all source files in build order"""
        result = []
        for library in self.libraries:
            for partition in self.partitions.get(library, []):
                result.extend(self.files.get((library, partition), []))
        return result
