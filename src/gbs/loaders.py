"""YAML loaders for GBS configuration files

Loads partition, library, repository, and project definitions from YAML files.
Supports pluggable repository loaders for custom formats.
"""

import yaml
import importlib
from pathlib import Path
from typing import Any, Callable, Protocol

from gbs.models import (
    SourceFile,
    Language,
    FilterCondition,
    ConditionalGroup,
    Partition,
    Library,
    Repository,
    ToolsuiteConfig,
    Project,
)
from gbs.logging import get_logger


logger = get_logger(__name__)


class LoadError(Exception):
    """Error loading configuration file"""
    pass


class RepositoryLoader(Protocol):
    """Protocol for repository loader plugins

    A repository loader must implement a `load` function that takes a Path
    and returns a Repository object.
    """

    def load(self, path: Path) -> Repository:
        """Load a repository from the given path

        Args:
            path: Path to the repository root or definition file

        Returns:
            Repository object

        Raises:
            LoadError: If repository cannot be loaded
        """
        ...


# Registry of repository loaders
_REPOSITORY_LOADERS: dict[str, Callable[[Path], Repository]] = {}


def register_repository_loader(name: str, loader: Callable[[Path], Repository]):
    """Register a custom repository loader

    Args:
        name: Fully qualified name for the loader (e.g., "gbs.plugin.nsl.tree")
        loader: Loader function that takes Path and returns Repository
    """
    _REPOSITORY_LOADERS[name] = loader
    logger.debug(f"Registered repository loader: {name}")


def get_repository_loader(name: str) -> Callable[[Path], Repository]:
    """Get a repository loader by name

    If the loader is not registered, attempts to import it as a module
    and use its `load` function.

    Args:
        name: Fully qualified loader name (e.g., "gbs.plugin.nsl.tree")

    Returns:
        Loader function

    Raises:
        LoadError: If loader cannot be found or imported
    """
    # Check if already registered
    if name in _REPOSITORY_LOADERS:
        return _REPOSITORY_LOADERS[name]

    # Try to import the module
    try:
        logger.debug(f"Importing repository loader: {name}")
        module = importlib.import_module(name)

        # Look for 'load' function
        if not hasattr(module, 'load'):
            raise LoadError(
                f"Repository loader '{name}' must provide a 'load' function"
            )

        loader = module.load

        # Register for future use
        register_repository_loader(name, loader)

        return loader

    except ImportError as e:
        raise LoadError(f"Failed to import repository loader '{name}': {e}")
    except Exception as e:
        raise LoadError(f"Error loading repository loader '{name}': {e}")


def load_yaml_file(path: Path) -> dict[str, Any]:
    """Load and parse a YAML file

    Args:
        path: Path to YAML file

    Returns:
        Parsed YAML data

    Raises:
        LoadError: If file cannot be loaded or parsed
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data is None:
                raise LoadError(f"Empty or invalid YAML file: {path}")
            return data
    except FileNotFoundError:
        raise LoadError(f"File not found: {path}")
    except yaml.YAMLError as e:
        raise LoadError(f"YAML parse error in {path}: {e}")
    except Exception as e:
        raise LoadError(f"Error loading {path}: {e}")


def load_sources(sources_data: list[dict[str, Any]], base_path: Path) -> list[SourceFile]:
    """Load source files from YAML data

    Args:
        sources_data: List of source specifications
        base_path: Base path for resolving relative file paths

    Returns:
        List of SourceFile objects
    """
    result = []

    for source_spec in sources_data:
        if "language" not in source_spec:
            raise LoadError("Source specification missing 'language' field")
        if "files" not in source_spec:
            raise LoadError("Source specification missing 'files' field")

        try:
            language = Language(source_spec["language"])
        except ValueError:
            raise LoadError(f"Unknown language: {source_spec['language']}")

        variant = source_spec.get("variant")

        for file_path in source_spec["files"]:
            result.append(SourceFile(
                path=base_path / file_path,
                language=language,
                variant=variant
            ))

    return result


def load_conditional_group(
    name: str,
    conditions_data: list[dict[str, Any]],
    base_path: Path
) -> ConditionalGroup:
    """Load a conditional group from YAML data

    Args:
        name: Group name
        conditions_data: List of condition specifications
        base_path: Base path for resolving file paths

    Returns:
        ConditionalGroup object
    """
    conditions = []

    for cond_data in conditions_data:
        if "condition" not in cond_data:
            raise LoadError(f"Condition in group '{name}' missing 'condition' field")

        expression = cond_data["condition"]
        deps = cond_data.get("deps", [])

        # Load sources
        sources_data = cond_data.get("sources", [])
        sources = load_sources(sources_data, base_path)

        # Load nested groups (recursive)
        nested_groups = []
        groups_data = cond_data.get("groups", {})
        for group_name, group_conditions in groups_data.items():
            nested_group = load_conditional_group(group_name, group_conditions, base_path)
            nested_groups.append(nested_group)

        condition = FilterCondition(
            expression=expression,
            deps=deps,
            sources=sources,
            groups=nested_groups
        )
        conditions.append(condition)

    return ConditionalGroup(name=name, conditions=conditions)


def load_partition(path: Path) -> Partition:
    """Load a partition definition from a YAML file

    The partition name is derived from the filename (basename without extension).
    The YAML content is parsed as a FilterCondition (root of the partition) with
    an implicit "condition: default".

    Args:
        path: Path to partition YAML file (.gbs.yaml)

    Returns:
        Partition object

    Raises:
        LoadError: If file cannot be loaded or is invalid
    """
    logger.debug(f"Loading partition from {path}")
    data = load_yaml_file(path)

    # Extract partition name from filename
    # my_partition.gbs.yaml -> my_partition
    name = path.stem
    if name.endswith('.gbs'):
        name = name[:-4]  # Remove .gbs if present (for .gbs.yaml files)

    # Base path for resolving source files (relative to partition file)
    base_path = path.parent

    # The root YAML is a FilterCondition with implicit "condition: default"
    # Parse sources at root level
    sources_data = data.get("sources", [])
    sources = load_sources(sources_data, base_path)

    # Parse deps at root level
    deps = data.get("deps", [])

    # Load nested groups (if any)
    nested_groups = []
    groups_data = data.get("groups", {})
    for group_name, group_conditions in groups_data.items():
        if not isinstance(group_conditions, list):
            raise LoadError(
                f"Group '{group_name}' in partition '{name}' must be a list of conditions"
            )
        nested_group = load_conditional_group(group_name, group_conditions, base_path)
        nested_groups.append(nested_group)

    # Create root FilterCondition (implicit "default" condition)
    root_condition = FilterCondition(
        expression="default",
        deps=deps,
        sources=sources,
        groups=nested_groups
    )

    # Wrap in a ConditionalGroup named "root"
    root_group = ConditionalGroup(name="root", conditions=[root_condition])

    # Create partition with the root group
    partition = Partition(name=name, groups=[root_group])
    logger.info(f"Loaded partition '{name}' from {path}")
    return partition


def load_library(path: Path, discover_partitions: bool = True) -> Library:
    """Load a library definition from a YAML file

    Args:
        path: Path to library YAML file
        discover_partitions: Whether to auto-load referenced partitions

    Returns:
        Library object

    Raises:
        LoadError: If file cannot be loaded or is invalid
    """
    logger.debug(f"Loading library from {path}")
    data = load_yaml_file(path)

    if "name" not in data:
        raise LoadError(f"Library file {path} missing 'name' field")

    name = data["name"]
    description = data.get("description")
    library = Library(name=name, description=description)

    # Load partitions
    if discover_partitions and "partitions" in data:
        base_path = path.parent

        for partition_spec in data["partitions"]:
            if isinstance(partition_spec, str):
                # Simple partition name/path
                partition_path = base_path / partition_spec
                if not partition_path.suffix:
                    # Add .gbs.yaml extension
                    partition_path = partition_path.with_suffix(".gbs.yaml")

                if partition_path.exists():
                    partition = load_partition(partition_path)
                    library.add_partition(partition)
                else:
                    logger.warning(f"Partition file not found: {partition_path}")

            elif isinstance(partition_spec, dict) and "pattern" in partition_spec:
                # Glob pattern for auto-discovery
                pattern = partition_spec["pattern"]
                matches = list(base_path.glob(pattern))
                logger.debug(f"Pattern '{pattern}' matched {len(matches)} files")

                for match_path in matches:
                    try:
                        partition = load_partition(match_path)
                        library.add_partition(partition)
                    except LoadError as e:
                        logger.warning(f"Failed to load partition from {match_path}: {e}")

    logger.info(f"Loaded library '{name}' with {len(library.partitions)} partitions")
    return library


def load_repository(path: Path, discover_libraries: bool = True) -> Repository:
    """Load a repository definition from a YAML file

    Args:
        path: Path to repository YAML file
        discover_libraries: Whether to auto-load referenced libraries

    Returns:
        Repository object

    Raises:
        LoadError: If file cannot be loaded or is invalid
    """
    logger.debug(f"Loading repository from {path}")
    data = load_yaml_file(path)

    if "name" not in data:
        raise LoadError(f"Repository file {path} missing 'name' field")

    name = data["name"]
    description = data.get("description")
    root = path.parent
    repository = Repository(name=name, root=root, description=description)

    # Load libraries
    if discover_libraries and "libraries" in data:
        for library_spec in data["libraries"]:
            if isinstance(library_spec, dict) and "path" in library_spec:
                # Explicit path
                lib_path = root / library_spec["path"]
                if lib_path.is_dir():
                    # Look for library definition file in directory
                    for filename in ["library.gbs.yaml", "library.gbs"]:
                        lib_file = lib_path / filename
                        if lib_file.exists():
                            lib_path = lib_file
                            break

                if lib_path.exists():
                    try:
                        library = load_library(lib_path)
                        repository.add_library(library)
                    except LoadError as e:
                        logger.warning(f"Failed to load library from {lib_path}: {e}")
                else:
                    logger.warning(f"Library file not found: {lib_path}")

            elif isinstance(library_spec, dict) and "pattern" in library_spec:
                # Glob pattern for auto-discovery
                pattern = library_spec["pattern"]
                matches = list(root.glob(pattern))
                logger.debug(f"Pattern '{pattern}' matched {len(matches)} files")

                for match_path in matches:
                    try:
                        library = load_library(match_path)
                        repository.add_library(library)
                    except LoadError as e:
                        logger.warning(f"Failed to load library from {match_path}: {e}")

    logger.info(f"Loaded repository '{name}' with {len(repository.libraries)} libraries")
    return repository


def load_project(path: Path) -> Project:
    """Load a project definition from a YAML file

    Args:
        path: Path to project YAML file

    Returns:
        Project object

    Raises:
        LoadError: If file cannot be loaded or is invalid
    """
    logger.debug(f"Loading project from {path}")
    data = load_yaml_file(path)

    # Required fields
    required_fields = ["name", "toolsuite", "topcell", "output_format", "root_library"]
    for field in required_fields:
        if field not in data:
            raise LoadError(f"Project file {path} missing required field '{field}'")

    name = data["name"]
    description = data.get("description")
    topcell = data["topcell"]
    output_format = data["output_format"]

    # Load toolsuite configuration
    toolsuite_data = data["toolsuite"]
    if "name" not in toolsuite_data or "backend" not in toolsuite_data:
        raise LoadError("Toolsuite must specify 'name' and 'backend'")

    toolsuite = ToolsuiteConfig(
        name=toolsuite_data["name"],
        backend=toolsuite_data["backend"],
        config=toolsuite_data.get("config", {})
    )

    # Filter variables
    filter_vars = data.get("filter_vars", {})

    # Load root library (inline definition)
    root_lib_data = data["root_library"]
    if "name" not in root_lib_data:
        raise LoadError("Root library must specify 'name'")

    root_library = Library(
        name=root_lib_data["name"],
        description=root_lib_data.get("description")
    )

    # Load root library partitions (inline)
    base_path = path.parent
    if "partitions" in root_lib_data:
        for partition_data in root_lib_data["partitions"]:
            if "name" not in partition_data:
                raise LoadError("Partition must specify 'name'")

            partition_name = partition_data["name"]

            # Parse inline partition as FilterCondition (same as external partition files)
            # Parse sources at root level
            sources_data = partition_data.get("sources", [])
            sources = load_sources(sources_data, base_path)

            # Parse deps at root level
            deps = partition_data.get("deps", [])

            # Load nested groups (if any)
            nested_groups = []
            groups_data = partition_data.get("groups", {})
            for group_name, group_conditions in groups_data.items():
                if not isinstance(group_conditions, list):
                    raise LoadError(
                        f"Group '{group_name}' in partition '{partition_name}' "
                        f"must be a list of conditions"
                    )
                nested_group = load_conditional_group(group_name, group_conditions, base_path)
                nested_groups.append(nested_group)

            # Create root FilterCondition (implicit "default" condition)
            root_condition = FilterCondition(
                expression="default",
                deps=deps,
                sources=sources,
                groups=nested_groups
            )

            # Wrap in a ConditionalGroup named "root"
            root_group = ConditionalGroup(name="root", conditions=[root_condition])

            # Create partition with the root group
            partition = Partition(name=partition_name, groups=[root_group])
            root_library.add_partition(partition)

    project = Project(
        name=name,
        root_library=root_library,
        toolsuite=toolsuite,
        topcell=topcell,
        output_format=output_format,
        filter_vars=filter_vars,
        description=description
    )

    logger.info(f"Loaded project '{name}' with topcell '{topcell}'")
    return project


def load_repositories_from_project(project_data: dict[str, Any], project_base_path: Path) -> list[Repository]:
    """Load repositories specified in a project file

    Args:
        project_data: Parsed project YAML data
        project_base_path: Base path for resolving relative repository paths

    Returns:
        List of loaded Repository objects

    Raises:
        LoadError: If any repository cannot be loaded
    """
    repositories = []

    if "repositories" not in project_data:
        return repositories

    for repo_spec in project_data["repositories"]:
        if not isinstance(repo_spec, dict):
            raise LoadError(f"Repository specification must be a dict, got {type(repo_spec)}")

        if "path" not in repo_spec:
            raise LoadError("Repository specification must include 'path'")

        repo_path = Path(repo_spec["path"])

        # Resolve relative paths
        if not repo_path.is_absolute():
            repo_path = project_base_path / repo_path

        # Get loader (default to YAML loader)
        loader_name = repo_spec.get("loader", None)

        if loader_name:
            # Use custom loader
            loader = get_repository_loader(loader_name)
            logger.info(f"Loading repository from {repo_path} using {loader_name}")
            repository = loader(repo_path)
        else:
            # Use default YAML loader
            logger.info(f"Loading repository from {repo_path} using default YAML loader")
            repository = load_repository(repo_path)

        repositories.append(repository)

    logger.info(f"Loaded {len(repositories)} repositories from project spec")
    return repositories


def load_project_with_repositories(path: Path) -> tuple[Project, list[Repository]]:
    """Load a project and its specified repositories

    Convenience function that loads a project and any repositories specified
    in the project file.

    Args:
        path: Path to project YAML file

    Returns:
        Tuple of (Project, list of Repository)

    Raises:
        LoadError: If project or repositories cannot be loaded
    """
    # Load project
    project = load_project(path)

    # Load YAML to get repository specs
    data = load_yaml_file(path)
    repositories = load_repositories_from_project(data, path.parent)

    return project, repositories
