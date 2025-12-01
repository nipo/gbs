"""Build Plan Executor

Executes BuildPlans created by the planner by running passes in order
with iteration until stabilization.
"""

from __future__ import annotations
from pathlib import Path

from .planner import BuildPlan
from .model.build import BuildContext, BuildFileSet
from .logging import get_logger


logger = get_logger(__name__)


class BuildPlanExecutor:
    """Executes a BuildPlan

    Executes passes in order, with iteration until the fileset stabilizes
    (no more modifications). Each pass transforms BuildResource inputs to
    BuildResource outputs, which are added to the fileset for subsequent passes.
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
        """
        logger.info(f"Executing build plan: {plan.output_group.name}")
        logger.info(f"Plan has {len(plan.passes)} passes")

        # Set topcell from output group onto project (for backward compatibility with context.get_topcell())
        # Store original topcell to restore later
        original_topcell = self.context.project.topcell
        self.context.project.topcell = plan.output_group.topcell

        # Create BuildFileSet from source fileset
        fileset = BuildFileSet(self.context)

        # Populate from sources
        self.context.populate_fileset(plan.source_fileset, fileset)

        logger.info(f"Source fileset populated: {len(fileset)} files")

        # Execute passes with iteration until stabilization
        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"Starting iteration {iteration}")

            # Track modification serial before iteration
            initial_serial = fileset.modification_serial

            # Execute each pass in the plan
            for pass_class in plan.passes:
                logger.info(f"Executing pass: {pass_class.name}")
                logger.debug(
                    f"  Input types: {pass_class.input_types}, "
                    f"Output types: {pass_class.output_types}"
                )

                # Determine backend module from pass class
                backend_module = self._get_backend_module(pass_class)

                # Get backend config for this pass
                backend_config = plan.backend_configs.get(backend_module, {})

                # Instantiate pass
                pass_instance = pass_class()

                # Filter inputs from fileset matching pass input_types
                inputs = []
                for input_type in pass_class.input_types:
                    matching = list(fileset.filter(file_type=input_type))
                    inputs.extend(matching)
                    logger.debug(f"  Found {len(matching)} inputs of type {input_type}")

                if not inputs:
                    logger.debug(f"  No inputs found for pass {pass_class.name}, skipping")
                    continue

                # Execute pass
                try:
                    outputs = await pass_instance.execute(self.context, inputs)
                    logger.debug(f"  Pass produced {len(outputs)} outputs")

                    # Add outputs to fileset
                    for output in outputs:
                        fileset.add(output)
                        logger.debug(f"    Added output: {output.path} ({output.file_type})")

                except Exception as e:
                    logger.error(f"Pass {pass_class.name} failed: {e}")
                    raise

            # Check if fileset was modified
            if fileset.modification_serial == initial_serial:
                logger.info(f"Fileset unchanged in iteration {iteration}, stopping")
                break

            logger.debug(
                f"Fileset modified (serial {initial_serial} -> {fileset.modification_serial}), "
                f"continuing iteration"
            )

        if iteration >= max_iterations:
            logger.warning(f"Reached maximum iterations ({max_iterations})")

        logger.info(f"Build plan execution complete ({iteration} iterations)")

        # Restore original topcell
        self.context.project.topcell = original_topcell

        return fileset

    @staticmethod
    def _get_backend_module(pass_class: type) -> str:
        """Determine backend module path from pass class

        Args:
            pass_class: Pass class

        Returns:
            Backend module path (e.g., "gbs.backend.ghdl")
        """
        module_path = pass_class.__module__
        # Pass classes are defined in get_backend(), which is in the backend module
        # Module path will be like "gbs.backend.ghdl"
        return module_path


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
