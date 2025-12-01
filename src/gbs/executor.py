"""Build Plan Executor

Executes BuildPlans created by the planner.

NOTE: This is a minimal implementation (Phase 5). Full backend conversion
and iteration logic are TODO items.
"""

from __future__ import annotations
from pathlib import Path

from .planner import BuildPlan
from .model.build import BuildContext, BuildFileSet
from .logging import get_logger


logger = get_logger(__name__)


class BuildPlanExecutor:
    """Executes a BuildPlan

    This is a minimal executor that demonstrates the execution architecture.
    Full implementation requires:
    - Complete Pass.execute() implementations in backends
    - Iteration until stabilization
    - Resource transformation tracking
    - Proper BuildFileSet population
    """

    def __init__(self, context: BuildContext):
        """Initialize executor

        Args:
            context: Build context
        """
        self.context = context

    async def execute_plan(self, plan: BuildPlan) -> BuildFileSet:
        """Execute a build plan

        Args:
            plan: BuildPlan to execute

        Returns:
            BuildFileSet with results

        Note:
            This is a stub implementation. Real execution requires:
            1. Converting SourceFileSet to BuildFileSet
            2. Executing each pass in order
            3. Transforming resources through the pass chain
            4. Iterating until stabilization
        """
        logger.info(f"Executing build plan: {plan.output_group.name}")
        logger.info(f"Plan has {len(plan.passes)} passes")

        # Create BuildFileSet from source fileset
        fileset = BuildFileSet(self.context)

        # Populate from sources
        self.context.populate_fileset(plan.source_fileset, fileset)

        logger.info(f"Source fileset populated: {len(fileset)} files")

        # Execute each pass
        # TODO: This is where the real pass execution would happen
        # For now, we just log what would be executed
        for pass_class in plan.passes:
            logger.info(f"Would execute pass: {pass_class.name}")
            logger.debug(
                f"  Input types: {pass_class.input_types}, "
                f"  Output types: {pass_class.output_types}"
            )

        logger.info(f"Build plan execution complete (stub)")

        return fileset


async def execute_project(
    context: BuildContext,
    plans: list[BuildPlan]
) -> dict[str, BuildFileSet]:
    """Execute all build plans for a project

    Args:
        context: Build context
        plans: List of BuildPlans to execute

    Returns:
        Dictionary mapping output group names to BuildFileSets
    """
    executor = BuildPlanExecutor(context)
    results = {}

    for plan in plans:
        logger.info(f"Executing output group: {plan.output_group.name}")
        fileset = await executor.execute_plan(plan)
        results[plan.output_group.name] = fileset

    return results
