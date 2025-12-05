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

from .passes import Pass, PassMetadata
from ..backend.protocol import Backend
from ..repository.model import Repository, SourceFileSet
from ..project.model import OutputGroup, OutputFile
from ..logging import get_logger


logger = get_logger(__name__)


class PlanningError(Exception):
    """Raised when build planning fails"""
    pass

class BuildPlan:
    """Result of build planning for one OutputGroup

    Contains the selected transformation chain (passes) and metadata
    needed to execute the plan.

    Attributes:
        output_group: The OutputGroup this plan is for
        passes: Ordered list of PassMetadata (in planning order)
        filter_vars: Merged filter variables from all passes + output_group
        repositories: List of repositories to search for sources
    """
    output_group: OutputGroup
    passes: list[PassMetadata]
    filter_vars: dict[str, Any]
    repositories: list[Repository]

    def __init__(self,
                 output_group: OutputGroup,
                 passes: list[PassMetadata],
                 filter_vars: dict[str, Any],
                 repositories: list[Repository]):
        self.output_group = output_group
        self.passes = passes
        self.filter_vars = filter_vars
        self.repositories = repositories
    
    def __str__(self) -> str:
        return (
            f"BuildPlan("
            f"output_group={self.output_group.name}, "
            f"{len(self.passes)} passes, "
            f"{len(self.filter_vars)} filter_vars)"
        )

@dataclass
class PartialPlan:
    outputs: set[str]
    passes: list[str]

    def __hash__(self):
        return hash((
            tuple(sorted(self.outputs)),
            tuple(sorted(p.name for p in self.passes)),
        ))

class BuildPlanner:
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
        backends: list[Backend]
    ):
        """Initialize planner

        Args:
            repositories: List of source repositories
            backends: List of backends that provide passes
        """
        self.repositories = repositories
        self.backends = backends
        self.logger = get_logger(f"{__name__}.BuildPlanner")

        # Compute available source file types
        self.available_source_types = set()
        for repo in repositories:
            self.available_source_types.update(repo.file_types)

        self.logger.debug(
            f"Initialized planner with {len(repositories)} repositories, "
            f"{len(backends)} backends"
        )
        self.logger.debug(
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
        self.logger.info(f"Planning build for output group: {output_group.name}")

        # Extract desired output types
        output_types = {output.type for output in output_group.outputs}

        # Get source types
        source_types = set(self.available_source_types)
        
        self.logger.debug(f"Planning path {sorted(source_types)} -> {sorted(output_types)}")

        initial_plan = PartialPlan(output_types, [])
        
        possibilities = self._progress_to_sources(output_group, source_types, initial_plan)

        selected = []
        for pp in possibilities:
            names = set(p.name for p in pp.passes)
            if set(output_group.require_passes) - names:
                continue
            if set(output_group.exclude_passes) & names:
                continue

            selected.append(pp)
        
        if not selected:
            raise PlanningError(f"Cannot find passes from {source_types} to {output_types}")

        if len(selected) != 1:
            raise PlanningError(f"Too many possibilities from {source_types} to {output_types}: {selected}")

        return BuildPlan(
            output_group = output_group,
            passes = selected[0].passes,
            filter_vars = self._combine_filter_vars(output_group, selected[0].passes),
            repositories = self.repositories)
        
    def _progress_to_sources(self,
                             output_group: OutputGroup,
                             target_inputs: set(str),
                             partial_plan: PartialPlan) -> list[PartialPlan]:
        candidates = self._query_backends(output_group, partial_plan.outputs)

        if not candidates:
            return []
        
        self.logger.debug(
            f"{' '*len(partial_plan.passes)} to go: {partial_plan.outputs} with {partial_plan.passes}"
        )
        self.logger.debug(f"{' '*len(partial_plan.passes)} candidates: {candidates}")

        ret = []
        for p in candidates:
            if p in partial_plan.passes:
                continue

            inputs_handled = partial_plan.outputs | p.input_types
            inputs_left = target_inputs - inputs_handled

            self.logger.debug(f"{' '*len(partial_plan.passes)} with {p}, inputs: {inputs_handled}, unsatisfied: {inputs_left}")

            n = PartialPlan(inputs_handled, partial_plan.passes + [p])
            if inputs_left:
                for sub in self._progress_to_sources(output_group, target_inputs, n):
                    ret.append(sub)
            else:
                ret.append(n)
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
            backend_config = output_group.backend_config.get(backend.name, {})

            # Ask backend for passes it can contribute
            passes = backend.contribute_passes(backend_config, desired_outputs)

            # Wrap in PassMetadata
            for pass_obj in passes:
                metadata = PassMetadata(
                    pass_obj=pass_obj,
                    config=backend_config,
                    backend_name=backend.name
                )
                candidates.append(metadata)

                self.logger.debug(
                    f"Backend {backend.name} contributed pass: {pass_obj.name}"
                )

        return candidates

    def _combine_filter_vars(
        self,
        output_group: OutputGroup,
        passes: set[PassMetadata]
    ) -> dict[str, Any]:
        """Combine filter variables from output group and all passes

        Filter variables from passes are merged with output_group.filter_vars.
        Later variables override earlier ones.

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
                self.logger.debug(
                    f"Pass {pass_meta.name} contributed filter_vars: "
                    f"{pass_meta.filter_vars}"
                )

        return combined
