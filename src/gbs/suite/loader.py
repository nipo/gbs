"""Suite loader

Functions for loading suite definitions from YAML files.
"""

from pathlib import Path
from typing import Optional
import yaml

from .model import (
    Suite, SuiteSettings, ProjectReference,
    FilterSettings, OutputSettings
)
from ..logging import get_logger

logger = get_logger(__name__)


class LoadError(Exception):
    """Error loading suite configuration"""
    pass


def load_suite(path: Path) -> Suite:
    """Load a suite definition from a YAML file

    Args:
        path: Path to suite.gbs.yaml file

    Returns:
        Suite instance

    Raises:
        LoadError: If suite cannot be loaded or is invalid

    Example:
        >>> suite = load_suite(Path("suite.gbs.yaml"))
        >>> print(f"Suite '{suite.name}' has {len(suite.projects)} projects")
    """
    logger.debug(f"Loading suite from {path}")

    if not path.exists():
        raise LoadError(f"Suite file not found: {path}")

    try:
        with open(path, 'r') as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise LoadError(f"Failed to parse YAML in {path}: {e}")
    except Exception as e:
        raise LoadError(f"Failed to read {path}: {e}")

    # Validate required fields
    if 'name' not in data:
        raise LoadError(f"Suite must have a 'name' field in {path}")

    if 'projects' not in data or not data['projects']:
        raise LoadError(f"Suite must have at least one project in {path}")

    # Parse suite metadata
    name = data['name']
    description = data.get('description')

    # Parse settings
    settings_data = data.get('settings', {})
    settings = _parse_settings(settings_data, path)

    # Parse project references
    projects_data = data.get('projects', [])
    projects = []
    project_names = set()

    suite_dir = path.parent

    for i, proj_data in enumerate(projects_data):
        try:
            project_ref = _parse_project_reference(proj_data, suite_dir, path)

            # Check for duplicate names
            if project_ref.name in project_names:
                raise LoadError(f"Duplicate project name '{project_ref.name}' in {path}")
            project_names.add(project_ref.name)

            projects.append(project_ref)
        except Exception as e:
            raise LoadError(f"Failed to parse project #{i+1} in {path}: {e}")

    # Validate dependencies
    _validate_dependencies(projects, path)

    suite = Suite(
        name=name,
        description=description,
        settings=settings,
        projects=projects,
        raw_config=data
    )

    logger.info(f"Loaded suite '{name}' with {len(projects)} projects")
    return suite


def _parse_settings(data: dict, suite_path: Path) -> SuiteSettings:
    """Parse suite settings from configuration data

    Args:
        data: Settings dictionary
        suite_path: Path to suite file (for relative path resolution)

    Returns:
        SuiteSettings instance
    """
    # Parse parallelism settings
    max_parallel_projects = data.get('max_parallel_projects', 4)
    max_parallel_tasks = data.get('max_parallel_tasks')

    if max_parallel_projects is not None:
        try:
            max_parallel_projects = int(max_parallel_projects)
            if max_parallel_projects < 1:
                logger.warning(f"max_parallel_projects must be >= 1, using default (4)")
                max_parallel_projects = 4
        except (ValueError, TypeError):
            logger.warning(f"Invalid max_parallel_projects value, using default (4)")
            max_parallel_projects = 4

    if max_parallel_tasks is not None:
        try:
            max_parallel_tasks = int(max_parallel_tasks)
            if max_parallel_tasks < 1:
                logger.warning(f"max_parallel_tasks must be >= 1, ignoring")
                max_parallel_tasks = None
        except (ValueError, TypeError):
            logger.warning(f"Invalid max_parallel_tasks value, ignoring")
            max_parallel_tasks = None

    # Parse stop/continue settings
    stop_on_failure = data.get('stop_on_failure', False)
    continue_on_error = data.get('continue_on_error', True)

    # Parse output settings
    output_data = data.get('output', {})
    output = _parse_output_settings(output_data, suite_path)

    # Parse filter settings
    filter_data = data.get('filter', {})
    filter_settings = _parse_filter_settings(filter_data, suite_path)

    return SuiteSettings(
        max_parallel_projects=max_parallel_projects,
        max_parallel_tasks=max_parallel_tasks,
        stop_on_failure=stop_on_failure,
        continue_on_error=continue_on_error,
        output=output,
        filter=filter_settings
    )


def _parse_output_settings(data: dict, suite_path: Path) -> OutputSettings:
    """Parse output settings from configuration data

    Args:
        data: Output settings dictionary
        suite_path: Path to suite file (for relative path resolution)

    Returns:
        OutputSettings instance
    """
    suite_dir = suite_path.parent

    # Parse output file paths (resolve relative to suite file)
    junit_xml = data.get('junit_xml')
    if junit_xml:
        junit_xml = Path(junit_xml)
        if not junit_xml.is_absolute():
            junit_xml = suite_dir / junit_xml

    summary_json = data.get('summary_json')
    if summary_json:
        summary_json = Path(summary_json)
        if not summary_json.is_absolute():
            summary_json = suite_dir / summary_json

    log_dir = data.get('log_dir')
    if log_dir:
        log_dir = Path(log_dir)
        if not log_dir.is_absolute():
            log_dir = suite_dir / log_dir

    save_logs = data.get('save_logs', True)
    log_level = data.get('log_level', 'INFO')
    tail_lines = data.get('tail_lines', 100)

    # Validate log level
    valid_levels = {'DEBUG', 'INFO', 'WARNING', 'ERROR'}
    if log_level not in valid_levels:
        logger.warning(f"Invalid log_level '{log_level}', using 'INFO'")
        log_level = 'INFO'

    # Validate tail_lines
    try:
        tail_lines = int(tail_lines)
        if tail_lines < 0:
            logger.warning(f"tail_lines must be >= 0, using 100")
            tail_lines = 100
    except (ValueError, TypeError):
        logger.warning(f"Invalid tail_lines value, using 100")
        tail_lines = 100

    return OutputSettings(
        junit_xml=junit_xml,
        summary_json=summary_json,
        log_dir=log_dir,
        save_logs=save_logs,
        log_level=log_level,
        tail_lines=tail_lines
    )


def _parse_filter_settings(data: dict, suite_path: Path) -> FilterSettings:
    """Parse filter settings from configuration data

    Args:
        data: Filter settings dictionary
        suite_path: Path to suite file (for relative path resolution)

    Returns:
        FilterSettings instance
    """
    suite_dir = suite_path.parent

    enabled = data.get('enabled', False)

    file_list = data.get('file_list')
    if file_list:
        file_list = Path(file_list)
        if not file_list.is_absolute():
            file_list = suite_dir / file_list

    files = data.get('files', [])
    if not isinstance(files, list):
        logger.warning(f"Filter 'files' must be a list, ignoring")
        files = []

    base_commit = data.get('base_commit')
    target_commit = data.get('target_commit')

    return FilterSettings(
        enabled=enabled,
        file_list=file_list,
        files=files,
        base_commit=base_commit,
        target_commit=target_commit
    )


def _parse_project_reference(data: dict, suite_dir: Path, suite_path: Path) -> ProjectReference:
    """Parse a project reference from configuration data

    Args:
        data: Project reference dictionary
        suite_dir: Directory containing suite file
        suite_path: Path to suite file (for error messages)

    Returns:
        ProjectReference instance

    Raises:
        LoadError: If project reference is invalid
    """
    # Validate required fields
    if 'name' not in data:
        raise LoadError(f"Project must have a 'name' field")

    if 'path' not in data:
        raise LoadError(f"Project '{data.get('name', '?')}' must have a 'path' field")

    name = data['name']
    path = Path(data['path'])

    # Resolve path relative to suite file
    if not path.is_absolute():
        path = suite_dir / path

    # Parse optional fields
    output_groups = data.get('output_groups')
    if output_groups is not None and not isinstance(output_groups, list):
        logger.warning(f"Project '{name}': output_groups must be a list, ignoring")
        output_groups = None

    max_parallel = data.get('max_parallel')
    if max_parallel is not None:
        try:
            max_parallel = int(max_parallel)
            if max_parallel < 1:
                logger.warning(f"Project '{name}': max_parallel must be >= 1, ignoring")
                max_parallel = None
        except (ValueError, TypeError):
            logger.warning(f"Project '{name}': Invalid max_parallel value, ignoring")
            max_parallel = None

    depends_on = data.get('depends_on', [])
    if not isinstance(depends_on, list):
        logger.warning(f"Project '{name}': depends_on must be a list, ignoring")
        depends_on = []

    tags = data.get('tags', [])
    if not isinstance(tags, list):
        logger.warning(f"Project '{name}': tags must be a list, ignoring")
        tags = []

    skip = data.get('skip', False)

    return ProjectReference(
        name=name,
        path=path,
        output_groups=output_groups,
        max_parallel=max_parallel,
        depends_on=depends_on,
        tags=tags,
        skip=skip
    )


def _validate_dependencies(projects: list[ProjectReference], suite_path: Path):
    """Validate project dependencies

    Checks that all referenced dependencies exist and there are no circular dependencies.

    Args:
        projects: List of project references
        suite_path: Path to suite file (for error messages)

    Raises:
        LoadError: If dependencies are invalid
    """
    project_names = {p.name for p in projects}

    # Check that all dependencies exist
    for project in projects:
        for dep_name in project.depends_on:
            if dep_name not in project_names:
                raise LoadError(
                    f"Project '{project.name}' depends on unknown project '{dep_name}' in {suite_path}"
                )

    # Check for circular dependencies using DFS
    def has_cycle(proj_name: str, visited: set, rec_stack: set) -> bool:
        """Check if there's a cycle starting from proj_name"""
        visited.add(proj_name)
        rec_stack.add(proj_name)

        # Get project
        proj = next(p for p in projects if p.name == proj_name)

        for dep_name in proj.depends_on:
            if dep_name not in visited:
                if has_cycle(dep_name, visited, rec_stack):
                    return True
            elif dep_name in rec_stack:
                return True

        rec_stack.remove(proj_name)
        return False

    visited = set()
    for project in projects:
        if project.name not in visited:
            if has_cycle(project.name, visited, set()):
                raise LoadError(f"Circular dependency detected in {suite_path}")


__all__ = [
    'LoadError',
    'load_suite',
]
