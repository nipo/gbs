"""GBS Project Data Models

Project-specific data structures for build configuration and output specification.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any
from ..repository.model import Repository, SourceFileSet

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
        target: Target device configuration (part number, device-specific settings)
        require_passes: Passes that MUST be in the build plan
        exclude_passes: Passes that MUST NOT be in the build plan
        require_backends: Backends that MUST contribute to the build plan
        exclude_backends: Backends that MUST NOT contribute to the build plan
        exclude_dispatchers: Generic dispatchers (by name) that MUST NOT hook
            into this build, even though plugins register them for every build

    Examples:
        >>> # Simulation output group
        >>> OutputGroup(
        ...     name="simulation",
        ...     topcell="testbench",
        ...     filter_vars={"sim": 1, "vendor": "generic"},
        ...     backend_config={"gbs.builtin.ghdl": {"vhdl_standard": "2008"}},
        ...     outputs=[OutputFile(type="simulator", path=Path("sim.exe"))]
        ... )
        >>>
        >>> # Synthesis output group with constraints
        >>> OutputGroup(
        ...     name="gowin_synth",
        ...     topcell="top",
        ...     filter_vars={"vendor": "gowin"},
        ...     require_backends=["gbs.builtin.gowin"],
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
    target: dict[str, Any] = field(default_factory=dict)  # Target device configuration
    partition: Optional[str] = None  # Root partition name (None = use default/only partition)

    # Build planning constraints
    require_passes: list[str] = field(default_factory=list)
    exclude_passes: list[str] = field(default_factory=list)
    require_backends: list[str] = field(default_factory=list)
    exclude_backends: list[str] = field(default_factory=list)
    exclude_dispatchers: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"OutputGroup({self.name}, topcell={self.topcell}, {len(self.outputs)} outputs)"


@dataclass
class ProjectModel:
    """A gateware project definition

    Projects define a single root partition and build configuration using output groups.
    The root partition is always placed in the "work" library (required by synthesis tools).

    Attributes:
        name: Project name
        root_partition_template: The project's root partition template (unevaluated)
        output_groups: List of output groups defining build targets and configurations
        description: Optional description
        raw_config: Raw configuration dictionary (for accessing backends etc)
        max_parallel: Maximum number of parallel tasks (None = use default from config)
    """
    name: str
    root_partition_templates: dict[str, Any]  # name -> PartitionTemplate
    output_groups: list[OutputGroup]
    description: Optional[str] = None
    raw_config: dict = field(default_factory=dict)
    max_parallel: Optional[int] = None  # Maximum parallel tasks

    def get_root_partition_template(self, output_group: OutputGroup) -> Any:
        """Get the root partition template for an output group.

        If the output group specifies a partition name, returns that template.
        Otherwise returns the only template (error if ambiguous).
        """
        if output_group.partition:
            if output_group.partition not in self.root_partition_templates:
                raise ValueError(
                    f"Output group '{output_group.name}' references unknown "
                    f"partition '{output_group.partition}'. "
                    f"Available: {list(self.root_partition_templates.keys())}"
                )
            return self.root_partition_templates[output_group.partition]

        if len(self.root_partition_templates) == 1:
            return next(iter(self.root_partition_templates.values()))

        raise ValueError(
            f"Output group '{output_group.name}' does not specify a partition, "
            f"but project has multiple root partitions: "
            f"{list(self.root_partition_templates.keys())}. "
            f"Add 'partition: <name>' to the output group."
        )

    @property
    def root_library_name(self) -> str:
        """Root library name. Sometimes, synthesis tools do not give a choice"""
        return "work"

    def __str__(self) -> str:
        return f"ProjectModel({self.name}, {len(self.output_groups)} output groups)"


__all__ = [
    'OutputFile',
    'OutputGroup',
    'ProjectModel',
]
