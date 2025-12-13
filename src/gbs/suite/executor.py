"""Suite executor

Orchestrates building multiple GBS projects in parallel with dependency management
and result collection.
"""

import asyncio
import time
import sys
from pathlib import Path
from typing import Optional, TextIO
from io import StringIO

from .model import (
    Suite, ProjectReference, ProjectResult, SuiteResult,
    ProjectStatus, SuiteStatus
)
from ..logging import get_logger
from ..ui import get_global_hub, BuildStatus
from ..ui.reporter import UIReporter

logger = get_logger(__name__)


class ExecutionError(Exception):
    """Error during suite execution"""
    pass


class SuiteExecutor(UIReporter):
    """Orchestrates building multiple projects with dependency management

    Example:
        >>> suite = load_suite(Path("suite.gbs.yaml"))
        >>> executor = SuiteExecutor(suite)
        >>> result = await executor.build_suite()
        >>> print(f"Built {result.successful}/{result.total_projects} projects")
    """

    def __init__(
        self,
        suite: Suite,
        gbs_config: Optional[any] = None,
        changed_files: Optional[set[Path]] = None
    ):
        """Initialize suite executor

        Args:
            suite: Suite definition
            gbs_config: Optional GBSConfig for project loading
            changed_files: Optional set of changed files for filtering
        """
        # Initialize UIReporter (no parent - top level)
        UIReporter.__init__(self,
            reporter_name=f"SuiteExecutor({suite.name})",
            parent_reporter=None
        )

        self.suite = suite
        self.gbs_config = gbs_config
        self.changed_files = changed_files or set()

    async def build_suite(self) -> SuiteResult:
        """Build all projects in the suite

        Returns:
            SuiteResult with detailed results for each project

        Raises:
            ExecutionError: If suite execution fails critically
        """
        start_time = time.time()
        project_results = []

        # Determine build order based on dependencies
        build_order = self._topological_sort()

        self.info(f"Building suite '{self.suite.name}' with {len(build_order)} projects")

        # Apply file-based filtering if enabled
        projects_to_build = build_order
        if self.suite.settings.filter.enabled and self.changed_files:
            projects_to_build = await self._filter_projects(build_order)
            self.info(f"File-based filtering: {len(projects_to_build)}/{len(build_order)} projects need rebuild")

        # Build projects in dependency order with parallelism
        semaphore = asyncio.Semaphore(self.suite.settings.max_parallel_projects)

        # Group projects by dependency level for parallel execution
        dependency_levels = self._get_dependency_levels(projects_to_build)

        for level_projects in dependency_levels:
            # Build all projects at this level in parallel
            tasks = [
                self._build_project_with_semaphore(proj_ref, semaphore)
                for proj_ref in level_projects
            ]

            level_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for proj_ref, result in zip(level_projects, level_results):
                if isinstance(result, Exception):
                    # Build failed with exception
                    project_results.append(ProjectResult(
                        project=proj_ref,
                        status=ProjectStatus.ERROR,
                        duration=0.0,
                        error_message=str(result)
                    ))

                    if self.suite.settings.stop_on_failure:
                        self.error(f"Stopping suite execution after error in '{proj_ref.name}'")
                        break
                else:
                    project_results.append(result)

                    if result.status == ProjectStatus.FAILURE and self.suite.settings.stop_on_failure:
                        self.error(f"Stopping suite execution after failure in '{proj_ref.name}'")
                        break

            # Check if we should stop
            if self.suite.settings.stop_on_failure:
                if any(r.status in (ProjectStatus.ERROR, ProjectStatus.FAILURE)
                       for r in project_results):
                    break

        # Add skipped projects
        built_project_names = {r.project.name for r in project_results}
        for proj_ref in build_order:
            if proj_ref.name not in built_project_names:
                project_results.append(ProjectResult(
                    project=proj_ref,
                    status=ProjectStatus.SKIPPED,
                    duration=0.0,
                    error_message="Skipped by file-based filtering"
                ))

        # Calculate overall status and statistics
        duration = time.time() - start_time

        total_projects = len(build_order)
        successful = sum(1 for r in project_results if r.status == ProjectStatus.SUCCESS)
        failed = sum(1 for r in project_results if r.status == ProjectStatus.FAILURE)
        errors = sum(1 for r in project_results if r.status == ProjectStatus.ERROR)
        skipped = sum(1 for r in project_results if r.status == ProjectStatus.SKIPPED)

        # Determine overall suite status
        if errors > 0:
            overall_status = SuiteStatus.ERROR
        elif failed > 0:
            overall_status = SuiteStatus.FAILURE
        elif skipped == total_projects:
            overall_status = SuiteStatus.SKIPPED
        else:
            overall_status = SuiteStatus.SUCCESS

        return SuiteResult(
            suite=self.suite,
            status=overall_status,
            duration=duration,
            project_results=project_results,
            total_projects=total_projects,
            successful=successful,
            failed=failed,
            errors=errors,
            skipped=skipped
        )

    async def _build_project_with_semaphore(
        self,
        proj_ref: ProjectReference,
        semaphore: asyncio.Semaphore
    ) -> ProjectResult:
        """Build a single project with semaphore control"""
        async with semaphore:
            return await self._build_project(proj_ref)

    async def _build_project(self, proj_ref: ProjectReference) -> ProjectResult:
        """Build a single project and capture results

        Args:
            proj_ref: Project reference to build

        Returns:
            ProjectResult with build status and timing
        """
        from ..project import Project

        start_time = time.time()

        self.info(f"Building project '{proj_ref.name}' at {proj_ref.path}")

        # Emit start status
        self.emit_build_status(
            status="started",
            target=proj_ref.name
        )

        try:
            # Find project file
            project_path = self._find_project_file(proj_ref.path)

            # Load project with self as parent reporter
            project = Project.load_from_file(project_path, gbs_config=self.gbs_config, parent_reporter=self)

            # Set suite-scoped output directory to prevent cross-contamination
            # Format: gbs-build/suite/<project_name>/
            suite_output_base = Path("gbs-build") / "suite" / proj_ref.name
            project.set_base_output_path(suite_output_base)

            # Override max_parallel if specified
            if proj_ref.max_parallel is not None:
                project.set_max_parallel(proj_ref.max_parallel)
            elif self.suite.settings.max_parallel_tasks is not None:
                project.set_max_parallel(self.suite.settings.max_parallel_tasks)

            # Capture output
            output_buffer = StringIO()
            log_file = None

            if self.suite.settings.output.save_logs and self.suite.settings.output.log_dir:
                log_dir = self.suite.settings.output.log_dir
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = log_dir / f"{proj_ref.name}.log"

            # Build project (capturing output is complex, for now just build)
            await project.build(show_progress=False)

            # Get source files for filtering
            source_files = set()
            try:
                sources_by_group = await project.get_source_files(proj_ref.output_groups)
                for files in sources_by_group.values():
                    source_files.update(files)
            except Exception as e:
                self.warning(f"Failed to get source files for '{proj_ref.name}': {e}")

            duration = time.time() - start_time

            self.info(f"✓ Built project '{proj_ref.name}' in {duration:.1f}s")

            # Emit success status
            self.emit_build_status(
                status="success",
                target=proj_ref.name,
                duration=duration
            )

            return ProjectResult(
                project=proj_ref,
                status=ProjectStatus.SUCCESS,
                duration=duration,
                log_file=log_file,
                source_files=source_files
            )

        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)

            self.error(f"✗ Failed to build project '{proj_ref.name}': {error_msg}")

            # Emit error status
            self.emit_build_status(
                status="error",
                target=proj_ref.name,
                duration=duration,
                message=error_msg
            )

            return ProjectResult(
                project=proj_ref,
                status=ProjectStatus.ERROR,
                duration=duration,
                error_message=error_msg
            )

    def _find_project_file(self, path: Path) -> Path:
        """Find project file from path (file or directory)

        Args:
            path: Path to project file or directory

        Returns:
            Path to project.gbs.yaml file

        Raises:
            ExecutionError: If project file not found
        """
        if path.is_file():
            return path
        elif path.is_dir():
            project_file = path / "project.gbs.yaml"
            if project_file.exists():
                return project_file
            raise ExecutionError(f"No project.gbs.yaml found in directory: {path}")
        else:
            raise ExecutionError(f"Project path does not exist: {path}")

    def _topological_sort(self) -> list[ProjectReference]:
        """Sort projects by dependencies (topological order)

        Returns:
            List of projects in build order (dependencies first)

        Raises:
            ExecutionError: If circular dependency detected
        """
        # Build dependency graph
        projects = {p.name: p for p in self.suite.projects if not p.skip}
        in_degree = {name: 0 for name in projects}

        for proj in projects.values():
            for dep in proj.depends_on:
                if dep in projects:
                    in_degree[proj.name] += 1

        # Kahn's algorithm
        queue = [name for name, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            # Sort queue for deterministic order
            queue.sort()
            name = queue.pop(0)
            result.append(projects[name])

            # Reduce in-degree for dependents
            for proj in projects.values():
                if name in proj.depends_on:
                    in_degree[proj.name] -= 1
                    if in_degree[proj.name] == 0:
                        queue.append(proj.name)

        if len(result) != len(projects):
            raise ExecutionError("Circular dependency detected in suite projects")

        return result

    def _get_dependency_levels(
        self,
        projects: list[ProjectReference]
    ) -> list[list[ProjectReference]]:
        """Group projects into dependency levels for parallel execution

        Projects at the same level have no dependencies on each other
        and can be built in parallel.

        Args:
            projects: Projects in topological order

        Returns:
            List of levels, each containing projects that can build in parallel
        """
        levels = []
        remaining = set(p.name for p in projects)
        project_map = {p.name: p for p in projects}

        while remaining:
            # Find projects with no unbuilt dependencies
            current_level = []
            for name in remaining:
                proj = project_map[name]
                if all(dep not in remaining for dep in proj.depends_on):
                    current_level.append(proj)

            if not current_level:
                raise ExecutionError("Circular dependency detected")

            levels.append(current_level)
            for proj in current_level:
                remaining.remove(proj.name)

        return levels

    async def _filter_projects(
        self,
        projects: list[ProjectReference]
    ) -> list[ProjectReference]:
        """Filter projects based on changed files

        Args:
            projects: All projects in build order

        Returns:
            Projects that need rebuilding
        """
        from ..project import Project

        if not self.changed_files:
            return projects

        projects_to_build = []
        skipped_projects = set()

        for proj_ref in projects:
            # Check if any dependency needs rebuild
            needs_rebuild_due_to_dep = any(
                dep in {p.name for p in projects_to_build}
                for dep in proj_ref.depends_on
            )

            if needs_rebuild_due_to_dep:
                # Dependency needs rebuild, so we need to rebuild this too
                projects_to_build.append(proj_ref)
                self.debug(f"Project '{proj_ref.name}': rebuild due to dependency")
                continue

            try:
                # Load project to check source files
                project_path = self._find_project_file(proj_ref.path)
                project = Project.load_from_file(project_path, gbs_config=self.gbs_config, parent_reporter=self)

                # Check if project needs rebuild
                always_rebuild = ["**/*.gbs.yaml", "**/project.gbs.yaml"]
                needs_rebuild, reason = await project.needs_rebuild(
                    self.changed_files,
                    output_group_names=proj_ref.output_groups,
                    always_rebuild_patterns=always_rebuild
                )

                if needs_rebuild:
                    projects_to_build.append(proj_ref)
                    self.debug(f"Project '{proj_ref.name}': {reason}")
                else:
                    skipped_projects.add(proj_ref.name)
                    self.debug(f"Project '{proj_ref.name}': {reason}")

            except Exception as e:
                # If we can't determine, rebuild to be safe
                self.warning(f"Failed to check if '{proj_ref.name}' needs rebuild: {e}")
                projects_to_build.append(proj_ref)

        return projects_to_build

    def list_projects(self) -> list[tuple[ProjectReference, Optional[Path]]]:
        """List all projects in the suite with their project files

        Returns:
            List of (ProjectReference, project_file_path) tuples.
            project_file_path is None if the file couldn't be found.
        """
        projects_with_files = []

        for proj_ref in self.suite.projects:
            try:
                project_file = self._find_project_file(proj_ref.path)
                projects_with_files.append((proj_ref, project_file))
            except ExecutionError as e:
                self.warning(f"Project '{proj_ref.name}': {e}")
                projects_with_files.append((proj_ref, None))

        return projects_with_files

    async def clean_suite(self, dry_run: bool = False) -> dict[str, Optional[str]]:
        """Clean all projects in the suite

        Uses the same base output path logic as build_suite().

        Args:
            dry_run: If True, show what would be cleaned without actually cleaning

        Returns:
            Dictionary mapping project name to error message (None if successful)
        """
        from ..project import Project

        results = {}

        for proj_ref in self.suite.projects:
            if proj_ref.skip:
                results[proj_ref.name] = "skipped"
                continue

            try:
                # Find project file
                project_path = self._find_project_file(proj_ref.path)

                # Load project with self as parent reporter
                project = Project.load_from_file(project_path, gbs_config=self.gbs_config, parent_reporter=self)

                # Set the SAME suite-scoped output directory as build_suite()
                # Format: gbs-build/suite/<project_name>/
                suite_output_base = Path("gbs-build") / "suite" / proj_ref.name
                project.set_base_output_path(suite_output_base)

                # Clean project
                await project.clean(dry_run=dry_run)

                results[proj_ref.name] = None  # Success
            except Exception as e:
                error_msg = str(e)
                self.warning(f"Failed to clean project '{proj_ref.name}': {error_msg}")
                results[proj_ref.name] = error_msg

        return results


__all__ = [
    'SuiteExecutor',
    'ExecutionError',
]
