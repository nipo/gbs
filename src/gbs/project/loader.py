"""Project loading and execution

Provides the Project class for loading, building, and managing GBS projects.
"""

from pathlib import Path
from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass, field

from .model import ProjectModel
from ..logging import get_logger

# Avoid circular imports by using TYPE_CHECKING
if TYPE_CHECKING:
    from ..repository.model import Repository

logger = get_logger(__name__)


class LoadError(Exception):
    """Error loading project or configuration file"""
    pass


def _lazy_imports():
    """Lazy import to avoid circular dependencies"""
    from ..repository.loader import (
        load_project as load_project_model,
        load_repositories_from_project,
    )
    return load_project_model, load_repositories_from_project


@dataclass
class Project:
    """GBS Project execution context

    Manages a loaded project including its data model, repositories, and
    build configuration. Provides methods for building and managing the project.

    Attributes:
        model: The project data model (ProjectModel)
        repositories: List of loaded repositories
        path: Path to the project file
        gbs_config: Optional GBS configuration
    """
    model: ProjectModel
    repositories: list = field(default_factory=list)  # list[Repository] but avoid import
    path: Optional[Path] = None
    gbs_config: Optional[any] = None

    @classmethod
    def load_from_file(cls, path: Path, gbs_config=None) -> 'Project':
        """Load a project from a YAML file

        This is the primary factory method for creating Project instances.
        Loads the project definition and any referenced repositories.

        Args:
            path: Path to project.gbs.yaml file
            gbs_config: Optional GBSConfig for repository merging

        Returns:
            Project instance

        Raises:
            LoadError: If project or repositories cannot be loaded

        Example:
            >>> proj = Project.load_from_file(Path("project.gbs.yaml"))
            >>> proj.build()
        """
        # Import loader functions lazily to avoid circular imports
        load_project_model, load_repositories_from_project = _lazy_imports()

        logger.info(f"Loading project: {path}")

        # Load the project data model
        try:
            project_model = load_project_model(path, gbs_config=gbs_config)
        except Exception as e:
            raise LoadError(f"Failed to load project from {path}: {e}")

        # Load repositories specified in the project
        try:
            repositories = load_repositories_from_project(
                project_model.raw_config,
                path.parent,
                gbs_config=gbs_config
            )
        except Exception as e:
            logger.warning(f"Failed to load repositories: {e}")
            repositories = []

        return cls(
            model=project_model,
            repositories=repositories,
            path=path,
            gbs_config=gbs_config
        )

    @classmethod
    def find_and_load(cls, start_path: Optional[Path] = None, gbs_config=None) -> 'Project':
        """Find and load a project from the current or parent directories

        Searches for project.gbs.yaml starting from start_path and walking
        up the directory tree.

        Args:
            start_path: Starting directory (defaults to current working directory)
            gbs_config: Optional GBSConfig

        Returns:
            Project instance

        Raises:
            LoadError: If no project file is found
        """
        if start_path is None:
            start_path = Path.cwd()

        current = start_path.resolve()

        # Walk up the directory tree
        while True:
            project_file = current / "project.gbs.yaml"
            if project_file.exists():
                return cls.load_from_file(project_file, gbs_config=gbs_config)

            # Move to parent directory
            parent = current.parent
            if parent == current:
                # Reached filesystem root
                break
            current = parent

        raise LoadError(f"No project.gbs.yaml found in {start_path} or parent directories")

    async def build(
        self,
        output_dir: Path = Path("build"),
        max_iterations: int = 10,
        show_progress: bool = True
    ):
        """Build the project

        Executes the build for all output groups.

        Args:
            output_dir: Output directory for build artifacts (default: "build")
            max_iterations: Maximum dispatcher iterations (default: 10)
            show_progress: Whether to show progress bars (default: True)

        Raises:
            Exception: If build fails

        Example:
            >>> proj = Project.load_from_file(Path("project.gbs.yaml"))
            >>> await proj.build()
        """
        from .builder import build_project

        await build_project(
            self.model,
            self.repositories,
            output_dir=output_dir,
            max_iterations=max_iterations,
            show_progress=show_progress,
            gbs_config=self.gbs_config
        )

    async def show_graph(
        self,
        output_dir: Path = Path("build"),
        max_iterations: int = 10
    ):
        """Show build dependency graph

        Displays detailed information about the build plan including source files,
        passes, outputs, library dependencies, and build task graph.

        Args:
            output_dir: Output directory for build artifacts (default: "build")
            max_iterations: Maximum dispatcher iterations (default: 10)

        Example:
            >>> proj = Project.load_from_file(Path("project.gbs.yaml"))
            >>> await proj.show_graph()
        """
        from .builder import show_graph_for_project

        await show_graph_for_project(
            self.model,
            self.repositories,
            output_dir=output_dir,
            max_iterations=max_iterations,
            gbs_config=self.gbs_config
        )

    def __str__(self) -> str:
        return f"Project({self.model.name}, {len(self.model.output_groups)} output groups, {len(self.repositories)} repositories)"


__all__ = [
    'LoadError',
    'Project',
]
