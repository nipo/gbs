"""Suite data models

Data models for test suites that orchestrate multiple GBS projects.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from enum import Enum


class SuiteStatus(Enum):
    """Status of a suite execution"""
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    SKIPPED = "skipped"


class ProjectStatus(Enum):
    """Status of a project within a suite"""
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class FilterSettings:
    """File-based filtering settings for smart test selection

    Attributes:
        enabled: Whether file-based filtering is enabled
        file_list: Path to file containing list of changed files (one per line)
        files: List of file paths to check (alternative to file_list)
        base_commit: Git commit to diff against (e.g., 'HEAD~1', 'origin/main')
        target_commit: Git commit to compare (default: working tree)
    """
    enabled: bool = False
    file_list: Optional[Path] = None
    files: list[str] = field(default_factory=list)
    base_commit: Optional[str] = None
    target_commit: Optional[str] = None


@dataclass
class OutputSettings:
    """Output configuration for suite execution

    Attributes:
        junit_xml: Path for JUnit XML output file
        summary_json: Path for summary JSON output file
        log_dir: Directory for individual project logs
        save_logs: Whether to save individual project logs
        log_level: Log level for project builds ('DEBUG', 'INFO', 'WARNING', 'ERROR')
        tail_lines: Number of lines to include in failure output (0 = all)
    """
    junit_xml: Optional[Path] = None
    summary_json: Optional[Path] = None
    log_dir: Optional[Path] = None
    save_logs: bool = True
    log_level: str = "INFO"
    tail_lines: int = 100


@dataclass
class SuiteSettings:
    """Global settings for suite execution

    Attributes:
        max_parallel_projects: Maximum number of projects to build in parallel
        max_parallel_tasks: Maximum number of tasks per project (overrides project config)
        stop_on_failure: Whether to stop suite execution on first project failure
        continue_on_error: Whether to continue building other projects after error
        output: Output configuration
        filter: File-based filtering configuration
    """
    max_parallel_projects: int = 4
    max_parallel_tasks: Optional[int] = None
    stop_on_failure: bool = False
    continue_on_error: bool = True
    output: OutputSettings = field(default_factory=OutputSettings)
    filter: FilterSettings = field(default_factory=FilterSettings)


@dataclass
class ProjectReference:
    """Reference to a project within a suite

    Attributes:
        name: Unique identifier for this project within the suite
        path: Path to project file or directory (resolved relative to suite file)
        output_groups: List of output groups to build (None = all)
        max_parallel: Override max_parallel for this project
        depends_on: List of project names this project depends on
        tags: Tags for grouping and filtering projects
        skip: Whether to skip this project
    """
    name: str
    path: Path
    output_groups: Optional[list[str]] = None
    max_parallel: Optional[int] = None
    depends_on: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    skip: bool = False


@dataclass
class Suite:
    """A collection of GBS projects to build and test together

    Attributes:
        name: Suite name
        description: Optional description
        settings: Suite execution settings
        projects: List of project references
        raw_config: Raw configuration dict (for extensions)
    """
    name: str
    description: Optional[str] = None
    settings: SuiteSettings = field(default_factory=SuiteSettings)
    projects: list[ProjectReference] = field(default_factory=list)
    raw_config: dict = field(default_factory=dict)


@dataclass
class ProjectResult:
    """Result of building a single project

    Attributes:
        project: The project reference that was built
        status: Build status
        duration: Build duration in seconds
        log_file: Path to log file (if saved)
        error_message: Error message (if status is ERROR or FAILURE)
        output_tail: Last N lines of output (for failure reporting)
        source_files: Set of source files used (for filtering)
    """
    project: ProjectReference
    status: ProjectStatus
    duration: float
    log_file: Optional[Path] = None
    error_message: Optional[str] = None
    output_tail: list[str] = field(default_factory=list)
    source_files: set[Path] = field(default_factory=set)


@dataclass
class SuiteResult:
    """Result of executing a suite

    Attributes:
        suite: The suite that was executed
        status: Overall suite status
        duration: Total execution time in seconds
        project_results: Results for each project
        total_projects: Total number of projects
        successful: Number of successful projects
        failed: Number of failed projects
        errors: Number of projects with errors
        skipped: Number of skipped projects
    """
    suite: Suite
    status: SuiteStatus
    duration: float
    project_results: list[ProjectResult] = field(default_factory=list)
    total_projects: int = 0
    successful: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0


__all__ = [
    'SuiteStatus',
    'ProjectStatus',
    'FilterSettings',
    'OutputSettings',
    'SuiteSettings',
    'ProjectReference',
    'Suite',
    'ProjectResult',
    'SuiteResult',
]
