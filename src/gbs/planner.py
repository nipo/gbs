"""Type-Based Build Planner

This module implements the iterative type-based build planner that finds
transformation paths from source file types to desired output file types.

The planner works backwards from outputs:
1. Query backends for passes that produce desired outputs
2. For each pass, check if inputs are satisfied by:
   - Available source file types, OR
   - Output types from other passes
3. Recursively plan for unsatisfied input types
4. Combine filter_vars from all selected passes
5. Return BuildPlan with pass chain and combined filter variables

The planner uses iterative deepening to find the shortest path.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path

from .model.passes import Pass, PassMetadata
from .model.backend import Backend
from .model.repository import Repository, OutputGroup, OutputFile, SourceFileSet
from .logging import get_logger


logger = get_logger(__name__)


class PlanningError(Exception):
    """Raised when build planning fails"""
    pass


@dataclass
class BuildPlan:
    """Result of build planning for one OutputGroup

    Contains the selected transformation chain (passes) and metadata
    needed to execute the plan.

    Attributes:
        output_group: The OutputGroup this plan is for
        passes: Ordered list of PassMetadata (planning order, not execution order!)
        combined_filter_vars: Merged filter variables from all passes + output_group
        source_fileset: Resolved source files (populated after planning)
        repositories: List of repositories to search for sources
    """
    output_group: OutputGroup
    passes: list[PassMetadata]
    combined_filter_vars: dict[str, Any]
    repositories: list[Repository]
    source_fileset: SourceFileSet | None = None

    def __str__(self) -> str:
        return (
            f"BuildPlan("
            f"output_group={self.output_group.name}, "
            f"{len(self.passes)} passes, "
            f"{len(self.combined_filter_vars)} filter_vars)"
        )


class BuildPlanner:
    """Type-based iterative build planner

    Finds transformation paths from available source file types to
    desired output file types by querying backends for passes and
    working backwards.

    Example:
        planner = BuildPlanner(repositories, backends)
        plan = planner.plan(output_group)
        print(f"Selected passes: {[p.name for p in plan.passes]}")
        print(f"Filter variables: {plan.combined_filter_vars}")
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
            ...     backend_config={"gbs.backend.ghdl": {"vhdl_standard": "2008"}},
            ...     outputs=[OutputFile(type="ghdl-simulator", path=Path("sim"))]
            ... )
            >>> plan = planner.plan(og)
        """
        self.logger.info(f"Planning build for output group: {output_group.name}")

        # Extract desired output types
        desired_outputs = {output.type for output in output_group.outputs}
        self.logger.debug(f"Desired output types: {sorted(desired_outputs)}")

        # Query backends for candidate passes
        candidate_passes = self._query_backends(output_group, desired_outputs)
        self.logger.debug(
            f"Found {len(candidate_passes)} candidate passes from backends"
        )

        if not candidate_passes:
            raise PlanningError(
                f"No passes found that can produce outputs: {desired_outputs}"
            )

        # Find transformation path
        selected_passes = self._find_transformation_path(
            desired_outputs,
            candidate_passes
        )

        if not selected_passes:
            raise PlanningError(
                f"No transformation path found from available sources "
                f"{self.available_source_types} to desired outputs {desired_outputs}"
            )

        self.logger.info(
            f"Selected {len(selected_passes)} passes: "
            f"{[p.name for p in selected_passes]}"
        )

        # Combine filter variables
        combined_filter_vars = self._combine_filter_vars(
            output_group,
            selected_passes
        )

        self.logger.debug(
            f"Combined filter variables: {combined_filter_vars}"
        )

        return BuildPlan(
            output_group=output_group,
            passes=selected_passes,
            combined_filter_vars=combined_filter_vars,
            repositories=self.repositories
        )

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
            pass_classes = backend.contribute_passes(backend_config, desired_outputs)

            # Wrap in PassMetadata
            for pass_class in pass_classes:
                metadata = PassMetadata(
                    pass_class=pass_class,
                    config=backend_config,
                    backend_name=backend.name
                )
                candidates.append(metadata)

                self.logger.debug(
                    f"Backend {backend.name} contributed pass: {pass_class.name}"
                )

        return candidates

    def _find_transformation_path(
        self,
        desired_outputs: set[str],
        candidates: list[PassMetadata]
    ) -> list[PassMetadata]:
        """Find transformation path from sources to outputs

        Uses iterative deepening to find shortest path.

        Args:
            desired_outputs: Set of desired output types
            candidates: List of candidate passes

        Returns:
            List of selected PassMetadata in planning order
        """
        self.logger.debug(
            f"Finding transformation path for outputs: {sorted(desired_outputs)}"
        )

        # Try to satisfy all output types
        selected = []
        satisfied_types = set(self.available_source_types)

        # Iteratively select passes that move us closer to outputs
        max_iterations = 10
        for iteration in range(max_iterations):
            self.logger.debug(
                f"Iteration {iteration + 1}: satisfied types = {sorted(satisfied_types)}"
            )

            # Check if we've satisfied all output types
            if desired_outputs.issubset(satisfied_types):
                self.logger.info("All output types satisfied!")
                return selected

            # Find passes whose inputs are satisfied
            available_passes = [
                p for p in candidates
                if p.input_types.issubset(satisfied_types)
            ]

            if not available_passes:
                self.logger.error(
                    f"No more passes available. Cannot satisfy: "
                    f"{desired_outputs - satisfied_types}"
                )
                return []

            # Select pass that provides needed outputs
            # Priority: passes that provide desired outputs first
            needed_outputs = desired_outputs - satisfied_types
            best_pass = None

            # First, look for passes that directly provide needed outputs
            for pass_meta in available_passes:
                if pass_meta.output_types & needed_outputs:
                    if pass_meta not in selected:
                        best_pass = pass_meta
                        break

            # If no pass directly provides needed outputs, look for intermediate types
            if not best_pass:
                # Find passes that provide types needed by other passes
                for pass_meta in available_passes:
                    if pass_meta not in selected:
                        best_pass = pass_meta
                        break

            if not best_pass:
                self.logger.error("No suitable pass found for next step")
                return []

            # Add pass to plan
            selected.append(best_pass)
            satisfied_types.update(best_pass.output_types)

            self.logger.debug(
                f"Selected pass: {best_pass.name} "
                f"(inputs: {best_pass.input_types}, outputs: {best_pass.output_types})"
            )

        self.logger.error(f"Max iterations ({max_iterations}) exceeded")
        return []

    def _combine_filter_vars(
        self,
        output_group: OutputGroup,
        selected_passes: list[PassMetadata]
    ) -> dict[str, Any]:
        """Combine filter variables from output group and all passes

        Filter variables from passes are merged with output_group.filter_vars.
        Later variables override earlier ones.

        Args:
            output_group: Output group with base filter_vars
            selected_passes: Selected passes that contribute filter_vars

        Returns:
            Combined dictionary of filter variables
        """
        combined = dict(output_group.filter_vars)

        for pass_meta in selected_passes:
            # Pass filter_vars override output_group filter_vars
            combined.update(pass_meta.filter_vars)

            if pass_meta.filter_vars:
                self.logger.debug(
                    f"Pass {pass_meta.name} contributed filter_vars: "
                    f"{pass_meta.filter_vars}"
                )

        return combined


def plan_project(
    project,
    repositories: list[Repository],
    backends: list[Backend]
) -> list[BuildPlan]:
    """Plan build for all output groups in a project

    Convenience function that creates a planner and plans all output groups.

    Args:
        project: Project with output_groups
        repositories: List of source repositories
        backends: List of backends

    Returns:
        List of BuildPlan, one per output group

    Raises:
        PlanningError: If any output group cannot be planned

    Example:
        >>> plans = plan_project(project, repositories, backends)
        >>> for plan in plans:
        ...     print(f"Output group: {plan.output_group.name}")
        ...     print(f"  Passes: {[p.name for p in plan.passes]}")
    """
    planner = BuildPlanner(repositories, backends)
    plans = []

    for output_group in project.output_groups:
        plan = planner.plan(output_group)
        plans.append(plan)

    return plans
