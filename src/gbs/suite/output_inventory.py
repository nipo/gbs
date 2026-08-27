"""Machine-readable inventory of the outputs a suite declares.

Backs ``gbs suite outputs``. Records come from
:class:`gbs.project.output_inventory.OutputInventory`, so a suite
document and a project document share one schema.
"""

from pathlib import Path
from typing import Optional

from ..logging import get_logger
from .executor import ExecutionError, SuiteExecutor
from .model import ProjectReference, Suite

logger = get_logger(__name__)


class SuiteOutputInventory:
    """The output-group records of every project a suite would build.

    Selection mirrors what ``gbs suite build`` would do: tag filters
    apply, skipped projects contribute nothing, and a project entry that
    restricts its output groups is described for those groups only.
    """

    def __init__(self,
                 suite: Suite,
                 gbs_config=None,
                 tags: Optional[list[str]] = None,
                 exclude_tags: Optional[list[str]] = None):
        """
        Args:
            suite: Loaded suite definition
            gbs_config: Optional GBSConfig for project loading
            tags: Only describe projects carrying one of these tags
            exclude_tags: Skip projects carrying one of these tags
        """
        self.gbs_config = gbs_config
        self.executor = SuiteExecutor(
            suite,
            gbs_config=gbs_config,
            tags=list(tags) if tags else None,
            exclude_tags=list(exclude_tags) if exclude_tags else None,
        )

    def records(self) -> list[dict]:
        """Build the records of every selected project, in suite-file order."""
        if self.executor.tags or self.executor.exclude_tags:
            self.executor.apply_tag_filter()

        records = []
        for proj_ref, project_file in self.executor.list_projects():
            if proj_ref.skip:
                logger.debug(f"Project '{proj_ref.name}' is skipped, not listing it")
                continue
            records.extend(self._project_records(proj_ref, project_file))
        return records

    def _project_records(self,
                         proj_ref: ProjectReference,
                         project_file: Optional[Path]) -> list[dict]:
        """Describe one suite entry.

        A project that cannot be found or loaded is an error in the suite
        file itself, unlike a project whose toolchain is merely absent
        from this machine, so it stops the listing instead of being
        reported as a record.
        """
        from ..project import Project
        from ..project.output_inventory import OutputInventory

        if project_file is None:
            raise ExecutionError(
                f"Project '{proj_ref.name}': no project file at {proj_ref.path}"
            )

        project = Project.load_from_file(project_file, gbs_config=self.gbs_config)
        inventory = OutputInventory(
            project,
            name=proj_ref.name,
            group_names=proj_ref.output_groups,
        )
        return inventory.records()


__all__ = ["SuiteOutputInventory"]
