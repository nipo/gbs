"""Build Planner for Output-Driven Build Planning

This module implements the build planning algorithm that finds paths from
source files to desired outputs through backend passes.

See doc/plan/build_system_refactoring.md for the complete design.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from collections import deque

from .model.repository import (
    Project,
    Repository,
    OutputGroup,
    SourceFileSet,
)
from .model.passes import Pass
from .backend.registry import BackendRegistry, PassInfo
from .resolver import DependencyResolver
from .logging import get_logger


logger = get_logger(__name__)


class BuildPlanError(Exception):
    """Error during build planning"""
    pass


@dataclass
class BuildPlan:
    """Execution plan for one output group

    Attributes:
        output_group: The OutputGroup this plan is for
        main_pass: Pass that contributes filter_vars for source enumeration
        source_fileset: Enumerated source files for this output group
        passes: List of passes in execution order (topologically sorted)
        backend_configs: Backend-specific configuration (module_path -> config)
    """
    output_group: OutputGroup
    main_pass: type[Pass]
    source_fileset: SourceFileSet
    passes: list[type[Pass]] = field(default_factory=list)
    backend_configs: dict[str, dict] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"BuildPlan({self.output_group.name}, "
            f"main={self.main_pass.name}, "
            f"{len(self.passes)} passes)"
        )


class BuildPlanner:
    """Creates build plans by finding paths from sources to outputs

    The planner:
    1. Tries each pass that could contribute to source enumeration
    2. Combines filter_vars and enumerates sources
    3. Builds capability graph from sources to outputs
    4. Finds paths to all desired outputs
    5. Applies constraints (require/exclude)
    6. Returns exactly one viable plan (errors if 0 or multiple)
    """

    def __init__(
        self,
        project: Project,
        repositories: list[Repository],
        registry: BackendRegistry
    ):
        """Initialize planner

        Args:
            project: Project to build
            repositories: Available repositories
            registry: Backend registry with available passes
        """
        self.project = project
        self.repositories = repositories
        self.registry = registry

    def plan(self, output_group: OutputGroup) -> BuildPlan:
        """Create execution plan for output group

        Args:
            output_group: Output group to plan

        Returns:
            BuildPlan for the output group

        Raises:
            BuildPlanError: If zero or multiple viable plans
        """
        logger.info(f"Planning build for output group: {output_group.name}")

        viable_plans = []

        # Get all passes that could contribute to source enumeration
        # For now, we consider all passes as potential main passes
        candidates = self._get_enumeration_candidates(output_group)

        logger.debug(f"Evaluating {len(candidates)} candidate passes")

        for pass_info in candidates:
            try:
                plan = self._try_plan_with_pass(output_group, pass_info)
                if plan:
                    viable_plans.append(plan)
                    logger.debug(
                        f"Viable plan found with {pass_info.full_name}: "
                        f"{len(plan.passes)} passes"
                    )
            except Exception as e:
                logger.debug(f"Plan with {pass_info.full_name} failed: {e}")
                continue

        # Check result
        if len(viable_plans) == 0:
            raise BuildPlanError(
                f"No viable build plan found for output group '{output_group.name}'. "
                f"Tried {len(candidates)} candidate passes. "
                f"Check that required backends are available and outputs are reachable."
            )

        if len(viable_plans) > 1:
            main_passes = [p.main_pass.name for p in viable_plans]
            raise BuildPlanError(
                f"Multiple viable plans for output group '{output_group.name}'. "
                f"Ambiguous main passes: {main_passes}. "
                f"Add constraints (require_backends/require_passes) to disambiguate."
            )

        plan = viable_plans[0]
        logger.info(
            f"Build plan created: {plan.output_group.name} with "
            f"{len(plan.passes)} passes"
        )
        return plan

    def _get_enumeration_candidates(self, output_group: OutputGroup) -> list[PassInfo]:
        """Get passes that could contribute to source enumeration

        For now, we consider passes that can produce any of the desired
        output types as potential main passes.

        Args:
            output_group: Output group

        Returns:
            List of candidate PassInfo objects
        """
        candidates = []

        # Find passes that can produce any of the desired output types
        for output_file in output_group.outputs:
            passes = self.registry.find_passes_by_output_type(output_file.type)
            for pass_info in passes:
                if pass_info not in candidates:
                    if self._pass_matches_constraints(pass_info, output_group):
                        candidates.append(pass_info)

        return candidates

    def _pass_matches_constraints(
        self,
        pass_info: PassInfo,
        output_group: OutputGroup
    ) -> bool:
        """Check if a pass matches the output group constraints

        Args:
            pass_info: Pass to check
            output_group: Output group with constraints

        Returns:
            True if pass matches constraints
        """
        # Check require_backends
        if output_group.require_backends:
            if pass_info.backend_module not in output_group.require_backends:
                return False

        # Check exclude_backends
        if output_group.exclude_backends:
            if pass_info.backend_module in output_group.exclude_backends:
                return False

        # Check require_passes
        if output_group.require_passes:
            if pass_info.full_name not in output_group.require_passes:
                return False

        # Check exclude_passes
        if output_group.exclude_passes:
            if pass_info.full_name in output_group.exclude_passes:
                return False

        return True

    def _try_plan_with_pass(
        self,
        output_group: OutputGroup,
        main_pass_info: PassInfo
    ) -> Optional[BuildPlan]:
        """Try to create a plan with a specific main pass

        Args:
            output_group: Output group to plan
            main_pass_info: Candidate main pass

        Returns:
            BuildPlan if viable, None otherwise
        """
        # Get backend config for this pass's backend
        backend_config = output_group.backend_config.get(
            main_pass_info.backend_module,
            {}
        )

        # Get filter vars contribution from the pass
        pass_instance = main_pass_info.pass_class()
        contributed_vars = pass_instance.contribute_filter_vars(backend_config)

        # Combine filter vars
        combined_vars = {
            **output_group.filter_vars,
            **contributed_vars
        }

        # Create temporary project with this output group's config
        temp_project = Project(
            name=self.project.name,
            root_partition=self.project.root_partition,
            topcell=output_group.topcell,
            filter_vars=combined_vars,
            raw_config=self.project.raw_config
        )

        # Enumerate sources using GBS dependency resolver
        resolver = DependencyResolver(temp_project, self.repositories)
        try:
            source_fileset = resolver.resolve()
        except Exception as e:
            logger.debug(f"Source enumeration failed: {e}")
            return None

        # Get source types available
        source_types = self._get_source_types(source_fileset)

        if not source_types:
            logger.debug("No source files found")
            return None

        # Find paths to all desired outputs
        all_passes = []
        for output_file in output_group.outputs:
            path = self._find_path_to_output(
                source_types=source_types,
                target_type=output_file.type,
                output_group=output_group,
                visited_passes=set()
            )

            if not path:
                logger.debug(
                    f"No path found from sources to {output_file.type}"
                )
                return None

            # Collect all passes in path
            for pass_info in path:
                if pass_info.pass_class not in all_passes:
                    all_passes.append(pass_info.pass_class)

        # Create build plan
        plan = BuildPlan(
            output_group=output_group,
            main_pass=main_pass_info.pass_class,
            source_fileset=source_fileset,
            passes=all_passes,
            backend_configs=output_group.backend_config.copy()
        )

        return plan

    def _get_source_types(self, source_fileset: SourceFileSet) -> set[str]:
        """Get all file types in the source fileset

        Args:
            source_fileset: Source file set

        Returns:
            Set of file type strings
        """
        types = set()
        for source_file in source_fileset.get_all_files():
            types.add(source_file.file_type)
        return types

    def _find_path_to_output(
        self,
        source_types: set[str],
        target_type: str,
        output_group: OutputGroup,
        visited_passes: set[str]
    ) -> Optional[list[PassInfo]]:
        """Find a path from sources to target output type

        Uses breadth-first search to find shortest path through passes.

        Args:
            source_types: Available source file types
            target_type: Desired output type
            output_group: Output group (for constraints)
            visited_passes: Passes already used (prevents loops)

        Returns:
            List of PassInfo in order, or None if no path found
        """
        # BFS to find shortest path
        queue = deque()

        # Start from passes that can consume our source types
        for source_type in source_types:
            passes = self.registry.find_passes_by_input_type(source_type)
            for pass_info in passes:
                if pass_info.full_name not in visited_passes:
                    if self._pass_matches_constraints(pass_info, output_group):
                        queue.append(([pass_info], pass_info.pass_class.output_types))

        while queue:
            path, current_types = queue.popleft()

            # Check if we reached the target
            if target_type in current_types:
                return path

            # Expand: find passes that can consume current types
            last_pass = path[-1]
            for output_type in current_types:
                next_passes = self.registry.find_passes_by_input_type(output_type)
                for pass_info in next_passes:
                    # Check for loops
                    if pass_info.full_name in visited_passes:
                        continue
                    if pass_info.full_name in [p.full_name for p in path]:
                        continue

                    # Check constraints
                    if not self._pass_matches_constraints(pass_info, output_group):
                        continue

                    # Add to queue
                    new_path = path + [pass_info]
                    new_types = pass_info.pass_class.output_types
                    queue.append((new_path, new_types))

        return None


def plan_project(
    project: Project,
    repositories: list[Repository],
    registry: BackendRegistry
) -> list[BuildPlan]:
    """Create build plans for all output groups in a project

    Args:
        project: Project to plan
        repositories: Available repositories
        registry: Backend registry

    Returns:
        List of BuildPlan, one per output group

    Raises:
        BuildPlanError: If planning fails for any output group
    """
    planner = BuildPlanner(project, repositories, registry)
    plans = []

    for output_group in project.output_groups:
        plan = planner.plan(output_group)
        plans.append(plan)

    return plans
