"""Type-Based Build Planner

This module implements the type-based build planner that finds transformation
paths from source file types to desired output file types.

The planner works backwards from outputs:

1. Query backends for passes that produce desired outputs
2. For each pass, check if inputs are satisfied by available source file types
3. Recursively plan for unsatisfied input types
4. Combine filter_vars from all selected passes
5. Return BuildPlan with pass chain and combined filter variables
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path

from .passes import PassMetadata
from ..protocol import Backend, Pass
from ..repository.model import Repository, SourceFileSet
from ..project.model import OutputGroup, OutputFile
from ..ui.reporter import UIReporter


def strip_type_suffixes(type_str: str) -> str:
    """Strip transform suffixes from a type string.

    Removes suffixes like "+gzip", "+base64" that are handled by
    post-processing dispatchers, not by the planner.

    Args:
        type_str: Type string like "ise-bitstream+gzip" or "gowin-fs"

    Returns:
        Base type without suffixes (e.g., "ise-bitstream")

    Examples:
        >>> strip_type_suffixes("ise-bitstream")
        "ise-bitstream"
        >>> strip_type_suffixes("ise-bitstream+gzip")
        "ise-bitstream"
        >>> strip_type_suffixes("gowin-fs+gzip+base64")
        "gowin-fs"
    """
    if '+' not in type_str:
        return type_str
    return type_str.split('+')[0]


class PlanningError(Exception):
    """Raised when build planning fails"""
    pass

class BuildPlan(UIReporter):
    """Result of build planning for one OutputGroup

    Contains the selected transformation chain (passes) and metadata
    needed to execute the plan.

    Attributes:
        output_group: The OutputGroup this plan is for
        passes: Ordered list of PassMetadata (in planning order)
        filter_vars: Merged filter variables from all passes + output_group
        repositories: List of repositories to search for sources
        types_with_library: Union of all file types requiring library classification
    """
    output_group: OutputGroup
    passes: list[PassMetadata]
    filter_vars: dict[str, Any]
    repositories: list[Repository]
    types_with_library: set[str]

    def __init__(self,
                 output_group: OutputGroup,
                 passes: list[PassMetadata],
                 filter_vars: dict[str, Any],
                 repositories: list[Repository],
                 types_with_library: set[str],
                 parent_reporter: Optional['UIReporter'] = None):
        # Initialize UIReporter
        UIReporter.__init__(
            self,
            reporter_name=f"Plan({output_group.name})",
            parent_reporter=parent_reporter
        )

        self.output_group = output_group
        self.passes = passes
        self.filter_vars = filter_vars
        self.repositories = repositories
        self.types_with_library = types_with_library

    def __str__(self) -> str:
        return (
            f"BuildPlan("
            f"output_group={self.output_group.name}, "
            f"{len(self.passes)} passes, "
            f"{len(self.filter_vars)} filter_vars)"
        )

@dataclass
class PartialPlan:
    required: set[str]
    acceptable: set[str]
    passes: list[str]

    def __hash__(self):
        return hash((
            tuple(sorted(self.outputs)),
            tuple(sorted(p.name for p in self.passes)),
        ))

class BuildPlanner(UIReporter):
    """Type-based iterative build planner

    Finds transformation paths from available source file types to
    desired output file types by querying backends for passes and
    working backwards.

    Example:
        planner = BuildPlanner(repositories, backends)
        plan = planner.plan(output_group)
        print(f"Selected passes: {[p.name for p in plan.passes]}")
        print(f"Filter variables: {plan.filter_vars}")
    """

    def __init__(
        self,
        repositories: list[Repository],
        backends: list[Backend],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None,
        root_partition_template: Any = None,
        parent_reporter: Optional['UIReporter'] = None
    ):
        """Initialize planner

        Args:
            repositories: List of source repositories
            backends: List of backends that provide passes
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)
            root_partition_template: Optional root partition template to include file types from
            parent_reporter: Optional parent UIReporter (typically a Project)
        """
        # Initialize UIReporter
        UIReporter.__init__(
            self,
            reporter_name="BuildPlanner",
            parent_reporter=parent_reporter
        )

        self.repositories = repositories
        self.backends = backends
        self.project_config = project_config or {}
        self.gbs_config = gbs_config

        # Compute available source file types
        self.available_source_types = set()
        for repo in repositories:
            self.available_source_types.update(repo.file_types())

        # Add file types from root partition template if provided
        if root_partition_template is not None:
            root_file_types = root_partition_template.get_all_file_types()
            self.available_source_types.update(root_file_types)
            self.debug(f"Added {len(root_file_types)} file types from root partition template")

        self.debug(
            f"Initialized planner with {len(repositories)} repositories, "
            f"{len(backends)} backends"
        )
        self.debug(
            f"Available source types: {sorted(self.available_source_types)}"
        )

    def plan(self, output_group: OutputGroup) -> BuildPlan:
        """Plan build for an output group

        Finds a transformation chain from available sources to desired outputs.

        Args:
            output_group: Output group specification

        Returns:
            BuildPlan with selected passes and combined filter variables

        Raises:
            PlanningError: If no valid plan can be found

        Example:
            >>> og = OutputGroup(
            ...     name="simulation",
            ...     topcell="testbench",
            ...     filter_vars={"target-usage": "simulation"},
            ...     backend_config={"gbs.builtin.ghdl": {"vhdl_standard": "2008"}},
            ...     outputs=[OutputFile(type="ghdl-simulator", path=Path("sim"))]
            ... )
            >>> plan = planner.plan(og)
        """
        self.info(f"Planning build for output group: {output_group.name}")

        # Extract desired output types, stripping transform suffixes
        # (e.g., "ise-bitstream+gzip" -> "ise-bitstream")
        # The suffixes are handled by post-processing dispatchers, not the planner
        raw_output_types = {output.type for output in output_group.outputs}
        output_types = {strip_type_suffixes(t) for t in raw_output_types}

        if output_types != raw_output_types:
            self.debug(
                f"Stripped transform suffixes: {sorted(raw_output_types)} -> {sorted(output_types)}"
            )

        # Get source types
        source_types = set(self.available_source_types)

        self.debug(f"Planning path {sorted(source_types)} -> {sorted(output_types)}")

        initial_plan = PartialPlan(required = output_types,
                                   acceptable = output_types,
                                   passes = [])

        possibilities = self._progress_to_sources(output_group, source_types, initial_plan)

        self.debug(f"-> {possibilities}")

        selected = []
        for pp in possibilities:
            names = set(p.name for p in pp.passes)
            backends = set(p.backend_name for p in pp.passes)
            if set(output_group.require_passes) - names:
                continue
            if set(output_group.exclude_passes) & names:
                continue
            if set(output_group.require_backends) - backends:
                continue
            if set(output_group.exclude_backends) & backends:
                continue

            selected.append(pp)

        # Prune non-minimal plans: a plan whose pass set is a strict
        # superset of another candidate's carries redundant passes
        pass_sets = [set(p.name for p in pp.passes) for pp in selected]
        selected = [pp for i, pp in enumerate(selected)
                    if not any(other < pass_sets[i] for other in pass_sets)]

        if not selected:
            raise PlanningError(f"Cannot find passes from {source_types} to {output_types}")

        if len(selected) != 1:
            raise PlanningError(f"Too many possibilities from {source_types} to {output_types}: {selected}")

        # Collect union of all types_with_library from all passes
        types_with_library = set()
        for pass_meta in selected[0].passes:
            types_with_library.update(pass_meta.types_with_library)

        return BuildPlan(
            output_group = output_group,
            passes = selected[0].passes,
            filter_vars = self._combine_filter_vars(output_group, selected[0].passes),
            repositories = self.repositories,
            types_with_library = types_with_library,
            parent_reporter = self)
        
    def _progress_to_sources(self,
                             output_group: OutputGroup,
                             source_types: set(str),
                             partial_plan: PartialPlan) -> list[PartialPlan]:
        if not (source_types - partial_plan.acceptable):
            return [partial_plan]

        self.debug(
            f"{' '*len(partial_plan.passes)} to go: {partial_plan.required}/{partial_plan.acceptable} with {partial_plan.passes}"
        )

        candidates = self._query_backends(output_group, partial_plan.acceptable)

        if not candidates:
            self.debug(f"No pass wanted to generate {partial_plan.acceptable}")
            return []

        self.debug(f"{' '*len(partial_plan.passes)} candidates: {candidates}")

        ret = []
        for p in candidates:
            if p in partial_plan.passes:
                self.debug(f"{' '*len(partial_plan.passes)} avoiding loop with {p}")
                continue

            # Check if this pass produces any of the types we can convert
            if not (p.output_types & partial_plan.acceptable):
                self.debug(f"{' '*len(partial_plan.passes)} does not fit {p}")
                continue

            # Calculate the new set of types we can accept
            # - Remove what this pass produces from required
            # - Add what this pass accepts as acceptable
            required = partial_plan.required - p.output_types
            acceptable = partial_plan.acceptable | p.input_types

            self.debug(f"{' '*len(partial_plan.passes)} with {p}, acceptable: {acceptable}, required: {required}")

            n = PartialPlan(acceptable = acceptable,
                            required = required,
                            passes = partial_plan.passes + [p])
            for sub in self._progress_to_sources(output_group, source_types, n):
                if (source_types & sub.acceptable) == source_types:
                    ret.append(sub)
        return ret
    
    def _query_backends(
        self,
        output_group: OutputGroup,
        desired_outputs: set[str]
    ) -> list[PassMetadata]:
        """Query all backends for passes that can help

        Args:
            output_group: Output group with backend configs
            desired_outputs: Set of desired output file types

        Returns:
            List of PassMetadata for candidate passes
        """
        candidates = []

        for backend in self.backends:
            # Get backend-specific config
            backend_config = output_group.backend_config.get(backend.name, {}).copy()

            # Merge output group's target into backend config
            # This makes target available to passes via self.config instead of self.project_config
            if output_group.target:
                backend_config['target'] = output_group.target

            # Ask backend for passes it can contribute
            passes = backend.contribute_passes(backend_config, desired_outputs, self.project_config, self.gbs_config)

            self.debug(
                f"Backend {backend.name} contributed passes: {passes}"
            )

            # Wrap in PassMetadata
            for pass_obj in passes:
                metadata = PassMetadata(
                    pass_obj=pass_obj,
                    config=backend_config,
                    backend_name=backend.name
                )
                candidates.append(metadata)

        return candidates

    def _combine_filter_vars(
        self,
        output_group: OutputGroup,
        passes: set[PassMetadata]
    ) -> dict[str, Any]:
        """Combine filter variables from output group and all passes

        Filter variables from passes are merged with output_group.filter_vars.
        Later variables override earlier ones. After that, every
        registered plugin's ``transform_filter_vars`` hook is invoked
        and its output is merged in, but only for keys not already
        present in the canonical set.

        Args:
            output_group: Output group with base filter_vars
            passes: Passes that contribute filter_vars

        Returns:
            Combined dictionary of filter variables
        """
        combined = dict(output_group.filter_vars)

        for pass_meta in passes:
            # Pass filter_vars override output_group filter_vars
            combined.update(pass_meta.filter_vars)

            if pass_meta.filter_vars:
                self.debug(
                    f"Pass {pass_meta.name} contributed filter_vars: "
                    f"{pass_meta.filter_vars}"
                )

        from ..plugins import get_plugin_registry
        registry = get_plugin_registry()
        for plugin in registry.get_all_plugins():
            extras = plugin.transform_filter_vars(combined)
            if not extras:
                continue
            self.debug(
                f"Plugin {plugin.name} contributed extra filter_vars: "
                f"{extras}"
            )
            for key, value in extras.items():
                if key in combined:
                    self.debug(
                        f"Plugin {plugin.name} tried to overwrite "
                        f"canonical {key}={combined[key]!r} with {value!r}; "
                        f"canonical value kept"
                    )
                    continue
                combined[key] = value

        return combined
