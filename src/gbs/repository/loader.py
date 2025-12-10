"""YAML loaders for GBS configuration files

Loads partition, library, repository, and project definitions from YAML files.
Supports pluggable repository loaders for custom formats.
"""

import yaml
from pathlib import Path
from typing import Any, Callable, Type

from .model import Repository
from ..logging import get_logger
from ..utils import expand_path


logger = get_logger(__name__)


class LoadError(Exception):
    """Error loading configuration file"""
    pass


class RepositoryLoader:
    """Base class for repository loader plugins

    Repository loaders are instantiated with a path and provide a load()
    method to load the repository from that path.
    """

    def __init__(self, path: Path):
        """Initialize repository loader

        Args:
            path: Path to the repository root or definition file
        """
        self.path = path

    def load(self) -> Repository:
        """Load a repository from the configured path

        Returns:
            Repository object

        Raises:
            LoadError: If repository cannot be loaded
        """
        raise NotImplementedError("Subclasses must implement load()")


# Registry of repository loaders
_REPOSITORY_LOADERS: dict[str, Type[RepositoryLoader]] = {}
_loaders_discovered: bool = False


def register_repository_loader(name: str, loader_class: Type[RepositoryLoader]):
    """Register a custom repository loader class

    Args:
        name: Fully qualified name for the loader (e.g., "nsl-tree")
        loader_class: RepositoryLoader class (not instance)
    """
    _REPOSITORY_LOADERS[name] = loader_class
    logger.debug(f"Registered repository loader: {name}")


def discover_repository_loaders():
    """Discover all repository loaders via the unified plugin system

    Iterates over all registered plugins and calls their
    enumerate_repository_parsers() methods to discover parsers.
    Each parser class is registered using register_repository_loader().
    """
    global _loaders_discovered

    if _loaders_discovered:
        return

    logger.info("Discovering repository loaders via plugin system...")

    # Import here to avoid circular dependency
    from ..plugins.loader import get_plugin_registry

    # Get the plugin registry (which auto-discovers plugins)
    plugin_registry = get_plugin_registry()

    # Iterate over each plugin and get its repository parser classes
    for plugin in plugin_registry.get_all_plugins():
        # Get repository parser classes provided by this plugin (dict of name -> class)
        parser_classes = plugin.enumerate_repository_parsers()

        # Register each parser class
        for name, loader_class in parser_classes.items():
            # Validate that it's a RepositoryLoader subclass
            if not isinstance(loader_class, type) or not issubclass(loader_class, RepositoryLoader):
                logger.warning(
                    f"Repository parser '{name}' from plugin {plugin.name} "
                    f"is not a RepositoryLoader subclass"
                )
                continue

            # Register the parser class
            register_repository_loader(name, loader_class)
            logger.debug(f"Registered repository parser: {name} from {plugin.name}")

    _loaders_discovered = True
    logger.info(f"Repository loader discovery complete: {len(_REPOSITORY_LOADERS)} loaders")


def get_repository_loader(name: str) -> Type[RepositoryLoader]:
    """Get a repository loader class by name

    On first call, discovers all repository loaders from the plugin system.

    Args:
        name: Loader name (e.g., "nsl-tree")

    Returns:
        RepositoryLoader class (not instance)

    Raises:
        LoadError: If loader is not registered
    """
    # Discover loaders from plugins on first use
    discover_repository_loaders()

    # Check if registered
    if name in _REPOSITORY_LOADERS:
        return _REPOSITORY_LOADERS[name]

    raise LoadError(
        f"Repository loader '{name}' not registered. "
        f"Available loaders: {', '.join(_REPOSITORY_LOADERS.keys()) if _REPOSITORY_LOADERS else 'none'}"
    )


def load_repository(path: Path, discover_libraries: bool = True) -> Repository:
    """Load a repository using the plugin system

    Delegates to the YAML repository loader plugin.

    Args:
        path: Path to repository file
        discover_libraries: Ignored (kept for backwards compatibility)

    Returns:
        Repository object

    Raises:
        LoadError: If file cannot be loaded or is invalid
    """
    # Use YAML loader from plugin
    loader_class = get_repository_loader("yaml")
    loader = loader_class(path)
    return loader.load()


def load_project(path: Path, gbs_config=None):
    """Load a project definition from a YAML file

    Args:
        path: Path to project YAML file
        gbs_config: Optional GBSConfig (unused, kept for API compatibility)

    Returns:
        Project object

    Raises:
        LoadError: If file cannot be loaded or is invalid
    """
    # Import here to avoid circular dependency
    from ..project.model import ProjectModel as Project, OutputGroup, OutputFile

    logger.debug(f"Loading project from {path}")

    # Load YAML file
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data is None:
                raise LoadError(f"Empty or invalid YAML file: {path}")
    except FileNotFoundError:
        raise LoadError(f"File not found: {path}")
    except yaml.YAMLError as e:
        raise LoadError(f"YAML parse error in {path}: {e}")
    except Exception as e:
        raise LoadError(f"Error loading {path}: {e}")

    # Required fields
    required_fields = ["name", "root", "output"]
    for field in required_fields:
        if field not in data:
            raise LoadError(f"Project file {path} missing required field '{field}'")

    name = data["name"]
    description = data.get("description")

    # Parse max_parallel setting
    max_parallel = data.get("max_parallel")
    if max_parallel is not None:
        try:
            max_parallel = int(max_parallel)
            if max_parallel < 1:
                logger.warning(f"max_parallel must be >= 1 in {path}, ignoring")
                max_parallel = None
        except (ValueError, TypeError):
            logger.warning(f"Invalid max_parallel value in {path}, ignoring")
            max_parallel = None

    # Load root partition (inline definition)
    # The root partition is always placed in the "work" library
    root_data = data["root"]
    base_path = path.parent

    # Parse root partition template
    from ..project.partition import parse_partition_template
    root_partition_template = parse_partition_template(root_data, base_path)

    # Parse output groups
    output_data = data.get("output", [])
    if not isinstance(output_data, list):
        raise LoadError("'output' must be a list of output groups")
    if not output_data:
        raise LoadError("Project must define at least one output group")

    output_groups = []
    for og_data in output_data:
        if not isinstance(og_data, dict):
            raise LoadError("Each output group must be a dictionary")

        # Required fields
        if "name" not in og_data:
            raise LoadError("Output group missing required field 'name'")
        if "topcell" not in og_data:
            raise LoadError(f"Output group '{og_data['name']}' missing required field 'topcell'")

        og_name = og_data["name"]
        og_topcell = og_data["topcell"]
        og_filter_vars = og_data.get("filter_vars", {})
        og_backend_config = og_data.get("backend_config", {})
        og_target = og_data.get("target", {})

        # Validate target if present
        if og_target:
            if not isinstance(og_target, dict):
                raise LoadError(f"Output group '{og_name}': 'target' must be a dictionary")
            if "part" not in og_target:
                raise LoadError(f"Output group '{og_name}': 'target' must specify 'part' (device part number)")

        # Parse output files
        og_outputs = []
        outputs_data = og_data.get("outputs", [])
        for output_spec in outputs_data:
            if not isinstance(output_spec, dict):
                raise LoadError(f"Output in group '{og_name}' must be a dictionary")
            if "type" not in output_spec:
                raise LoadError(f"Output in group '{og_name}' missing 'type'")
            if "path" not in output_spec:
                raise LoadError(f"Output in group '{og_name}' missing 'path'")

            output_file = OutputFile(
                type=output_spec["type"],
                path=Path(output_spec["path"])
            )
            og_outputs.append(output_file)

        # Parse constraints
        og_require_passes = og_data.get("require_passes", [])
        og_exclude_passes = og_data.get("exclude_passes", [])
        og_require_backends = og_data.get("require_backends", [])
        og_exclude_backends = og_data.get("exclude_backends", [])

        output_group = OutputGroup(
            name=og_name,
            topcell=og_topcell,
            filter_vars=og_filter_vars,
            backend_config=og_backend_config,
            outputs=og_outputs,
            target=og_target,
            require_passes=og_require_passes,
            exclude_passes=og_exclude_passes,
            require_backends=og_require_backends,
            exclude_backends=og_exclude_backends
        )
        output_groups.append(output_group)

    project = Project(
        name=name,
        root_partition_template=root_partition_template,
        output_groups=output_groups,
        description=description,
        raw_config=data,
        max_parallel=max_parallel
    )

    logger.info(f"Loaded project '{name}' with {len(output_groups)} output groups")
    return project


def _load_single_repository(repo_spec: dict[str, Any], project_base_path: Path) -> Repository | None:
    """Load a single repository from a specification

    Args:
        repo_spec: Repository specification dict
        project_base_path: Base path for resolving relative paths

    Returns:
        Repository object, or None if loading failed

    Raises:
        LoadError: If repository specification is invalid
    """
    if not isinstance(repo_spec, dict):
        raise LoadError(f"Repository specification must be a dict, got {type(repo_spec)}")

    if "path" not in repo_spec:
        raise LoadError("Repository specification must include 'path'")

    # Expand ~ and environment variables in path
    repo_path = expand_path(repo_spec["path"])

    # Resolve relative paths
    if not repo_path.is_absolute():
        repo_path = project_base_path / repo_path

    # Get loader (default to YAML loader)
    loader_name = repo_spec.get("loader", None)

    try:
        if loader_name:
            # Use custom loader - get the class and instantiate it with path
            loader_class = get_repository_loader(loader_name)
            logger.info(f"Loading repository from {repo_path} using {loader_name}")
            loader_instance = loader_class(repo_path)
            repository = loader_instance.load()
        else:
            # Use default YAML loader
            logger.info(f"Loading repository from {repo_path} using default YAML loader")
            repository = load_repository(repo_path)

        return repository
    except LoadError as e:
        logger.warning(f"Failed to load repository from {repo_path}: {e}")
        return None


def load_repositories_from_project(project_data: dict[str, Any], project_base_path: Path, gbs_config=None) -> list[Repository]:
    """Load repositories specified in a project file

    Merges repositories from multiple sources:
    1. Config-level repositories (from GBSConfig)
    2. Project-level repositories (from project file)

    Args:
        project_data: Parsed project YAML data
        project_base_path: Base path for resolving relative repository paths
        gbs_config: Optional GBSConfig for merging config repositories

    Returns:
        List of loaded Repository objects

    Raises:
        LoadError: If any repository cannot be loaded
    """
    repositories = []

    # 1. Load config-level repositories first
    if gbs_config is not None and gbs_config.repositories:
        logger.debug(f"Loading {len(gbs_config.repositories)} config-level repositories")
        for repo_spec in gbs_config.repositories:
            repo = _load_single_repository(repo_spec, project_base_path)
            if repo:
                repositories.append(repo)

    # 2. Load project-level repositories
    if "repositories" in project_data:
        logger.debug(f"Loading project-level repositories")
        for repo_spec in project_data["repositories"]:
            repo = _load_single_repository(repo_spec, project_base_path)
            if repo:
                repositories.append(repo)

    logger.info(f"Loaded {len(repositories)} repositories total (config + project)")
    return repositories


def load_project_with_repositories(path: Path, gbs_config=None) -> tuple[Any, list[Repository]]:
    """Load a project and its specified repositories

    Convenience function that loads a project and any repositories specified
    in the project file and config.

    Args:
        path: Path to project YAML file
        gbs_config: Optional GBSConfig for repository merging

    Returns:
        Tuple of (Project, list of Repository)

    Raises:
        LoadError: If project or repositories cannot be loaded
    """
    # Load project
    project = load_project(path, gbs_config=gbs_config)

    # Use the project's raw_config instead of reloading the YAML file
    repositories = load_repositories_from_project(project.raw_config, path.parent, gbs_config=gbs_config)

    return project, repositories
