"""Machine-readable inventory of the outputs a project declares.

Backs ``gbs project outputs`` and, through
:class:`gbs.suite.output_inventory.SuiteOutputInventory`, ``gbs suite
outputs``. Both emit the same record schema so their documents
concatenate.
"""

from typing import Optional

from ..logging import get_logger
from .model import OutputGroup

logger = get_logger(__name__)


class OutputInventory:
    """The output-group records of one loaded project.

    Determining which backends an output group relies on means planning
    it, so a project whose tool is missing from this machine cannot be
    fully described. That is reported per record rather than raised: a
    suite routinely holds projects for toolchains the local machine does
    not have, and the declared outputs are known regardless.
    """

    def __init__(self,
                 project,
                 name: str,
                 group_names: Optional[list[str]] = None):
        """
        Args:
            project: Loaded :class:`gbs.project.Project`
            name: Identity to stamp on every record. The project's own
                name when standing alone, the suite-local name when the
                project is reached through a suite.
            group_names: Output groups to describe, None for all
        """
        self.project = project
        self.name = name
        self.group_names = group_names

    def records(self) -> list[dict]:
        """Build one record per selected output group, in project-file order."""
        return [self._record(og) for og in self._selected_groups()]

    def _selected_groups(self) -> list[OutputGroup]:
        """Resolve ``group_names`` against the project's output groups."""
        groups = self.project.model.output_groups
        if self.group_names is None:
            return groups

        by_name = {og.name: og for og in groups}
        unknown = [n for n in self.group_names if n not in by_name]
        if unknown:
            known = ", ".join(sorted(by_name))
            raise ValueError(
                f"Project '{self.name}' does not declare output group(s) "
                f"{', '.join(unknown)}. Known groups: {known}"
            )
        return [by_name[n] for n in self.group_names]

    def _planner(self, output_group: OutputGroup):
        """Build a planner scoped to one output group's root partition."""
        from ..planner.planner import BuildPlanner
        from ..plugins import get_plugin_registry

        return BuildPlanner(
            self.project.repositories,
            get_plugin_registry().get_all_backends(),
            self.project.model.raw_config,
            self.project.gbs_config,
            root_partition_template=self.project.model.get_root_partition_template(
                output_group
            ),
        )

    def _record(self, output_group: OutputGroup) -> dict:
        """Describe one output group.

        Keys that carry no value are left out entirely, so a consumer can
        tell "not declared" from "declared empty".
        """
        from ..planner.planner import PlanningError

        record = {
            "project": self.name,
            "group": output_group.name,
            "topcell": output_group.topcell,
        }

        part = output_group.target.get("part")
        if part:
            record["part"] = part
        if output_group.partition:
            record["partition"] = output_group.partition

        try:
            plan = self._planner(output_group).plan(output_group)
        except PlanningError as e:
            # The full diagnostic lists every candidate chain and every
            # probe rejection — far too much for a listing. Its first
            # line states what could not be reached; the rest goes to
            # the log, the same trade-off SuiteExecutor makes.
            message = str(e)
            logger.debug(
                f"Planning failed for '{self.name}' output group "
                f"'{output_group.name}':\n{message}"
            )
            record["error"] = message.split("\n", 1)[0]
        else:
            record["backends"] = sorted({pm.backend_name for pm in plan.passes})

        record["outputs"] = [
            {"type": of.type, "path": str(of.path)}
            for of in output_group.outputs
        ]
        return record


__all__ = ["OutputInventory"]
