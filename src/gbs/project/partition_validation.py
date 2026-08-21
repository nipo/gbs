"""Analysis-only validation of a single repository partition.

Validation answers one question: does the partition's dependency tree
resolve, and does a validator accept the sources it can read? It runs
the same planner, resolver and build machinery a project build uses,
against a synthetic output group asking for a validation report, with
no project root partition and no terminal artifact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .model import OutputFile, OutputGroup, ProjectModel
from .project import Project
from ..logging import get_logger
from ..repository.model import Partition
from ..validation_report import (
    VALIDATION_REPORT_FILE_TYPE,
    ValidationReport,
)

logger = get_logger(__name__)


class PartitionValidationError(Exception):
    """Validation could not be carried out at all.

    Raised when the partition does not exist, its dependencies do not
    resolve, or no validator can be planned — as opposed to the analyzer
    reporting problems in the sources, which is a normal result carried
    by the report.
    """
    pass


class PartitionValidationProject(Project):
    """Project whose root partition is looked up in a repository.

    Nothing here comes from a project file: the root partition is named
    on the command line and resolved from the loaded repositories once
    the plan's filter variables are known, and the library is the one
    the partition already belongs to rather than the project's "work".
    """

    # The validators only read the file types they understand; the rest
    # of the resolved set is reported as skipped instead of blocking the
    # plan.
    partial_source_coverage = True

    def __init__(
        self,
        partition_name: str,
        user_filter_vars: dict[str, Any],
        model: ProjectModel,
        repositories: list,
        gbs_config: Optional[Any],
    ):
        super().__init__(
            model=model,
            repositories=repositories,
            path=None,
            gbs_config=gbs_config,
        )
        self.partition_name = partition_name
        self.user_filter_vars = user_filter_vars

    @property
    def root_library_name(self) -> str:
        return self.partition_name.split('.', 1)[0]

    def planner_root_template(self, output_group) -> None:
        """No project-side root partition contributes file types here."""
        return None

    def filter_vars_finalize(self, plan) -> None:
        """Give the command line the last word on filter variables.

        Passes contribute variables that describe the tool they will
        run (vhdl_std, vhdl_frontend); a variable given with -f is a
        deliberate selection choice by the user and outranks them.
        """
        plan.filter_vars.update(self.user_filter_vars)

    def root_partitions(self, output_group, plan) -> list[Partition]:
        for repository in self.repositories:
            partition = repository.partition_lookup(
                self.partition_name, plan.filter_vars
            )
            if partition is not None:
                logger.info(
                    f"Found partition '{self.partition_name}' in "
                    f"repository '{repository.name}'"
                )
                return [partition]

        known = ", ".join(r.name for r in self.repositories) or "(none)"
        raise PartitionValidationError(
            f"Partition '{self.partition_name}' not found in any repository "
            f"(searched: {known})"
        )


class PartitionValidation:
    """Assembles and runs one partition validation."""

    OUTPUT_GROUP_NAME = "validate"

    def __init__(
        self,
        partition_name: str,
        repositories: list,
        gbs_config: Optional[Any],
        report_path: Path,
        project_data: Optional[dict] = None,
        filter_vars: Optional[dict[str, Any]] = None,
        backend_config: Optional[dict[str, dict]] = None,
        require_backends: Optional[list[str]] = None,
    ):
        self.partition_name = partition_name
        self.repositories = repositories
        self.gbs_config = gbs_config
        self.report_path = report_path
        self.project_data = project_data or {}
        self.filter_vars = filter_vars or {}
        self.backend_config = backend_config or {}
        self.require_backends = require_backends or []

    @classmethod
    def default_report_path(cls) -> Path:
        """Where the report goes when the caller wants it on stdout.

        The report is a build output written by a task, so it always
        exists as a file; printing it is a separate step.
        """
        return Path("gbs-build") / cls.OUTPUT_GROUP_NAME / "validation-report.yaml"

    def output_group(self) -> OutputGroup:
        """Synthetic output group asking for the validation report."""
        return OutputGroup(
            name=self.OUTPUT_GROUP_NAME,
            topcell=self.partition_name.split('.', 1)[-1],
            partition=self.partition_name,
            filter_vars=dict(self.filter_vars),
            backend_config=self.backend_config,
            outputs=[OutputFile(
                type=VALIDATION_REPORT_FILE_TYPE,
                path=self.report_path,
            )],
            require_backends=list(self.require_backends),
        )

    def project(self) -> PartitionValidationProject:
        model = ProjectModel(
            name=f"validate({self.partition_name})",
            root_partition_templates={},
            output_groups=[self.output_group()],
            raw_config=self.project_data,
        )
        return PartitionValidationProject(
            partition_name=self.partition_name,
            user_filter_vars=self.filter_vars,
            model=model,
            repositories=self.repositories,
            gbs_config=self.gbs_config,
        )

    async def run(self) -> ValidationReport:
        """Plan, resolve, analyze and report.

        Returns the report describing the run. A failing analysis is
        reported, not raised: the dependency tree and compile order come
        from the resolver and are worth showing even then.

        Raises:
            PartitionValidationError: The run could not get as far as
                producing a report.
        """
        from ..build.task import BuildError
        from ..planner.planner import PlanningError
        from ..repository.resolver import ResolutionError

        project = self.project()

        try:
            realizations = [r async for r in project.realizations()]
        except (PlanningError, ResolutionError) as e:
            raise PartitionValidationError(str(e))

        realization, = realizations

        error = None
        try:
            await realization.execute()
        except BuildError as e:
            error = str(e) or "analysis failed"

        report = ValidationReport(realization.build_ctx, error=error)

        if error is not None:
            # The report task never ran: its inputs are the analyses,
            # and one of them failed.
            report.write(self.report_path)

        return report


__all__ = [
    "PartitionValidation",
    "PartitionValidationError",
    "PartitionValidationProject",
]
