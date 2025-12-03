# GBS Project Module Refactoring - Design Document

## Overview

Consolidate all project-related functionality into a new `gbs.project` module, creating a clean separation between:
- **Repository concerns**: Library/partition structure, dependency resolution
- **Project concerns**: Build configuration, execution, output management

## Goals

1. **Clean API**: `proj = Project.load_from_file(...); proj.build()`
2. **Separation of Concerns**: Project model separate from repository model
3. **Self-contained**: Project manages its own context, config, plans
4. **Silent-capable**: Use `logger.info()` instead of `click.echo()` for optional output
5. **Testable**: All logic accessible without CLI

## New Module Structure

```
gbs/
├── project/
│   ├── __init__.py          # Public API exports
│   ├── model.py             # Project, OutputGroup, OutputSpec dataclasses
│   ├── loader.py            # Loading from YAML files
│   ├── builder.py           # Build execution logic
│   └── planner.py           # Build planning (optional split from builder)
├── repository/
│   ├── model.py             # Repository, Library, Partition (no Project!)
│   ├── loader.py            # Repository loading only
│   └── resolver.py          # Dependency resolution
└── cli/
    ├── project.py           # Thin CLI wrappers
    └── __init__.py          # Minimal helper functions
```

## Data Model Changes

### Current State (gbs/repository/model.py)
```python
@dataclass
class Project:
    name: str
    root_partition: Partition
    output_groups: list[OutputGroup]
    description: Optional[str] = None
    raw_config: dict = field(default_factory=dict)

    @property
    def root_library_name(self) -> str:
        return "work"
```

### New State (gbs/project/model.py)
```python
@dataclass
class ProjectModel:
    """Project data model (what's loaded from YAML)"""
    name: str
    root_partition: Partition
    output_groups: list[OutputGroup]
    description: Optional[str] = None
    raw_config: dict = field(default_factory=dict)

    @property
    def root_library_name(self) -> str:
        return "work"


class Project:
    """Project with build context and execution capability

    This is the main entry point for loading and building projects.
    Combines project model, configuration, repositories, and build execution.

    Attributes:
        model: ProjectModel (data loaded from YAML)
        project_file: Path to project file
        gbs_config: GBSConfig instance
        repositories: List of loaded repositories
        build_context: BuildContext (created on demand)
        plans: Build plans (created during planning phase)
    """

    def __init__(
        self,
        model: ProjectModel,
        project_file: Path,
        gbs_config: GBSConfig,
        repositories: list[Repository]
    ):
        self.model = model
        self.project_file = project_file
        self.gbs_config = gbs_config
        self.repositories = repositories
        self.build_context: Optional[BuildContext] = None
        self.plans: list[BuildPlan] = []
        self.logger = get_logger("Project")

    # === Class Methods (Factories) ===

    @classmethod
    def load_from_file(
        cls,
        project_file: Path,
        additional_repos: tuple[Path] = (),
        gbs_config: Optional[GBSConfig] = None
    ) -> 'Project':
        """Load project from YAML file

        Args:
            project_file: Path to *.gbs.yaml file
            additional_repos: Additional repository paths to load
            gbs_config: GBS configuration (auto-loads if None)

        Returns:
            Project instance ready to build

        Raises:
            LoadError: If project file is invalid or missing
        """
        # Implementation in gbs/project/loader.py

    @classmethod
    def find_and_load(
        cls,
        directory: Path = None,
        gbs_config: Optional[GBSConfig] = None
    ) -> 'Project':
        """Find and load project in directory (auto-discovery)

        Looks for *.gbs.yaml files. If exactly one found, loads it.

        Args:
            directory: Directory to search (default: current directory)
            gbs_config: GBS configuration (auto-loads if None)

        Returns:
            Project instance

        Raises:
            LoadError: If no project or multiple projects found
        """

    # === Properties ===

    @property
    def name(self) -> str:
        return self.model.name

    @property
    def output_groups(self) -> list[OutputGroup]:
        return self.model.output_groups

    @property
    def root_partition(self) -> Partition:
        return self.model.root_partition

    @property
    def root_library_name(self) -> str:
        return self.model.root_library_name

    # === Build Operations ===

    async def build(
        self,
        output_dir: Path = Path("build"),
        max_iterations: int = 100,
        show_progress: bool = True,
        output_group_name: Optional[str] = None
    ) -> None:
        """Build the project

        Args:
            output_dir: Build output directory
            max_iterations: Maximum backend iterations
            show_progress: Show progress bars (if TTY)
            output_group_name: Build specific output group (None = all)

        Raises:
            BuildError: If build fails
        """
        # Implementation in gbs/project/builder.py

    async def show_graph(
        self,
        output_dir: Path = Path("build"),
        max_iterations: int = 100
    ) -> None:
        """Show build dependency graph without building

        Args:
            output_dir: Build output directory (for planning)
            max_iterations: Maximum backend iterations
        """
        # Implementation in gbs/project/builder.py

    def clean(
        self,
        output_dir: Path = Path("build"),
        dry_run: bool = False,
        force: bool = False
    ) -> None:
        """Clean build artifacts

        Args:
            output_dir: Build output directory to clean
            dry_run: Show what would be deleted without deleting
            force: Skip confirmation prompt
        """
        # Implementation in gbs/project/builder.py

    # === Planning (Internal) ===

    def _plan_build(
        self,
        output_dir: Path,
        output_group_name: Optional[str] = None
    ) -> list[BuildPlan]:
        """Create build plans for output groups

        Internal method called by build() and show_graph().
        """
```

## Code Migration Map

### From `gbs/repository/model.py`

**Move to `gbs/project/model.py`**:
- `class Project` → becomes `ProjectModel` (data only)
- `class OutputGroup`
- `class OutputSpec`

**Keep in `gbs/repository/model.py`**:
- `class Repository`
- `class Library`
- `class Partition`
- `class ConditionalGroup`
- `class FilterCondition`
- `class SourceFile`
- `class SourceFileSet`

### From `gbs/repository/loader.py`

**Move to `gbs/project/loader.py`**:
```python
# These become class methods on Project:
load_project(path) → Project.load_from_file()
load_project_with_repositories(path, ...) → Part of load_from_file()
find_project_file() → Project.find_and_load()

# Helper functions (can stay as module functions):
_load_project_yaml(path) → stays
_parse_project_yaml(data, path) → stays
```

**Keep in `gbs/repository/loader.py`**:
```python
load_repository(path) → stays
LoadError → move to gbs/project/loader.py (or shared exceptions module)
```

### From `gbs/cli/__init__.py`

**Move to `gbs/project/loader.py`** or **make obsolete**:
```python
load_project_for_command(ctx, file, repos) → Becomes part of Project.load_from_file()
get_project_file(ctx) → CLI helper, stays or simplifies
find_project_file() → Project.find_and_load()
```

### From `gbs/cli/project.py`

**Move to `gbs/project/builder.py`**:
```python
_build_with_output_groups(...) → Project.build() method
_show_task_graph(ctx, fileset) → Part of Project.show_graph()

# The CLI build command becomes:
async def build(ctx, ...):
    try:
        project = Project.load_from_file(
            project_file,
            additional_repos=repo,
            gbs_config=ctx.obj["gbs_config"]
        )
        await project.build(
            output_dir=output_dir,
            max_iterations=max_iterations,
            show_progress=ctx.obj["allow_progress_bars"]
        )
    except LoadError as e:
        logger.error(f"Failed to load project: {e}")
        sys.exit(1)
```

## Implementation in `gbs/project/builder.py`

```python
"""Project build execution logic"""

from pathlib import Path
from typing import Optional
import sys

from ..build import BuildContext, BuildFileSet
from ..planner.planner import plan_project
from ..backend.registry import get_backend_registry
from ..backend.dispatcher import DispatcherRegistry, run_dispatcher_iteration
from ..repository.resolver import resolve_project
from ..repository.model import Repository, Library
from ..logging import get_logger


async def build_project(
    project: 'Project',  # Forward reference
    output_dir: Path,
    max_iterations: int,
    show_progress: bool,
    output_group_name: Optional[str] = None
) -> None:
    """Build a project (implementation of Project.build())

    This is the core build logic extracted from cli/project.py::_build_with_output_groups
    """
    logger = project.logger

    # Create build context
    build_ctx = BuildContext(project=project.model, gbs_config=project.gbs_config)

    # Discover backends
    logger.info("Discovering backends...")
    backend_registry = get_backend_registry()
    backend_names = backend_registry.list_backends()
    logger.info(f"Discovered {len(backend_names)} backend(s)")
    for backend_module in backend_names:
        logger.debug(f"  - {backend_module}")

    # Plan build for all output groups
    logger.info(f"Planning build for {len(project.output_groups)} output group(s)...")
    backends = backend_registry.get_all_backends()

    # Create synthetic repository from project's root partition
    project_repo = Repository(name=project.name, root=project.project_file.parent)
    project_library = Library(name=project.root_library_name)
    project_library.add_partition(project.root_partition)
    project_repo.add_library(project_library)

    # Include project repo in repositories
    all_repositories = [project_repo] + project.repositories

    # Filter output groups if specific name requested
    if output_group_name:
        output_groups = [g for g in project.output_groups if g.name == output_group_name]
        if not output_groups:
            raise ValueError(f"Output group '{output_group_name}' not found")
    else:
        output_groups = project.output_groups

    plans = plan_project_with_groups(
        project.model,
        output_groups,
        all_repositories,
        backends
    )

    # Resolve sources for each plan
    for plan in plans:
        build_set = resolve_project(project.model, all_repositories)
        plan.source_fileset = build_set

        num_files = len(plan.source_fileset.get_all_files())
        num_libs = len(plan.source_fileset.libraries)
        logger.info(f"  Output group '{plan.output_group.name}':")
        logger.info(f"    Topcell: {plan.output_group.topcell}")
        logger.info(f"    Sources: {num_files} files in {num_libs} libraries")
        logger.info(f"    Passes: {len(plan.passes)}")
        logger.info(f"    Outputs: {len(plan.output_group.outputs)}")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Execute each build plan
    logger.info("Executing build plans...")

    for plan in plans:
        logger.info(f"Building output group '{plan.output_group.name}'...")

        # Set topcell context
        build_ctx.set_output_group_context(
            topcell=plan.output_group.topcell,
            topcell_library=project.root_library_name
        )

        # Create BuildFileSet from plan's source fileset
        fileset = BuildFileSet(build_ctx)
        build_ctx.populate_fileset(plan.source_fileset, fileset)

        # Determine which backends to use
        backend_modules_used = set()

        # Add backends that contributed passes
        for pass_metadata in plan.passes:
            for backend_module in backend_names:
                backend = backend_registry.get_backend(backend_module)
                contributed_passes = backend.contribute_passes(
                    plan.output_group.backend_config.get(backend_module, {}),
                    {output.type for output in plan.output_group.outputs}
                )
                if pass_metadata.pass_class in contributed_passes:
                    backend_modules_used.add(backend_module)
                    break

        # Add backends explicitly configured in backend_config
        for backend_module in plan.output_group.backend_config.keys():
            if backend_module in backend_names:
                backend_modules_used.add(backend_module)

        # Create dispatchers
        dispatcher_registry = DispatcherRegistry()
        for backend_module in backend_modules_used:
            backend = backend_registry.get_backend(backend_module)
            backend_config = plan.output_group.backend_config.get(backend_module, {})
            backend_config['output_dir'] = str(output_dir)
            dispatcher = backend.create_dispatcher(backend_config)
            dispatcher_registry.register(dispatcher)
            logger.info(f"  Registered dispatcher: {dispatcher.name}")

        # Run dispatcher iteration
        iterations = await run_dispatcher_iteration(
            build_ctx,
            fileset,
            dispatcher_registry,
            max_iterations=max_iterations
        )
        logger.info(f"  Converged after {iterations} iteration(s)")

        # Execute build tasks
        logger.info("  Executing build tasks...")
        num_files = await build_ctx.execute_build(
            fileset,
            show_progress=(sys.stdout.isatty() and show_progress)
        )
        logger.info(f"  Processed {num_files} files")

    logger.info("Build complete!")


def plan_project_with_groups(project_model, output_groups, repositories, backends):
    """Plan build for specific output groups (subset of project)"""
    # Similar to plan_project but allows filtering output groups
    from ..planner.planner import plan_project
    # May need to extend planner to support this
    pass
```

## New CLI Interface

### `gbs/cli/project.py` (Simplified)

```python
"""GBS Project Commands - Thin CLI wrappers"""

import asyncclick as click
from pathlib import Path
import sys

from ..logging import get_logger
from ..project import Project, LoadError


@click.group(invoke_without_command=False, cls=ReMatchGroup)
@click.option("-f", "--file", "project_file", type=click.Path(exists=True, path_type=Path))
@click.pass_context
async def project(ctx, project_file: Path | None):
    """Project management commands"""
    ctx.obj["project_file_option"] = project_file


@project.command()
@click.option("-r", "--repo", type=click.Path(exists=True, path_type=Path), multiple=True)
@click.option("-o", "--output-dir", type=click.Path(path_type=Path), default=Path("build"))
@click.option("--max-iterations", type=int, default=100)
@click.option("--show-graph", is_flag=True)
@click.option("-g", "--output-group", type=str, help="Build specific output group")
@click.pass_context
async def build(ctx, repo, output_dir, max_iterations, show_graph, output_group):
    """Build a project"""
    logger = get_logger()
    project_file = ctx.obj.get("project_file_option")

    try:
        # Load project
        project = Project.load_from_file(
            project_file=project_file,
            additional_repos=repo,
            gbs_config=ctx.obj["gbs_config"]
        )

        # Show graph or build
        if show_graph:
            await project.show_graph(
                output_dir=output_dir,
                max_iterations=max_iterations
            )
        else:
            await project.build(
                output_dir=output_dir,
                max_iterations=max_iterations,
                show_progress=ctx.obj["allow_progress_bars"],
                output_group_name=output_group
            )

    except LoadError as e:
        logger.error(f"Failed to load project: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception("Build failed")
        sys.exit(1)


@project.command()
@click.option("-o", "--output-dir", type=click.Path(path_type=Path), default=Path("build"))
@click.option("--dry-run", is_flag=True)
@click.option("-f", "--force", is_flag=True)
@click.pass_context
async def clean(ctx, output_dir, dry_run, force):
    """Clean build artifacts"""
    logger = get_logger()
    project_file = ctx.obj.get("project_file_option")

    try:
        project = Project.load_from_file(
            project_file=project_file,
            gbs_config=ctx.obj["gbs_config"]
        )

        project.clean(
            output_dir=output_dir,
            dry_run=dry_run,
            force=force
        )

    except LoadError as e:
        logger.error(f"Failed to load project: {e}")
        sys.exit(1)
```

**Reduction**: ~400 lines → ~70 lines!

## Migration Strategy

### Phase 1: Create New Module Structure ✓
- [x] Create `gbs/project/` directory
- [x] Create empty module files

### Phase 2: Move Data Models
1. Copy `Project`, `OutputGroup`, `OutputSpec` from `repository/model.py` to `project/model.py`
2. Rename `Project` → `ProjectModel` in new location
3. Create new `Project` class with build capability
4. Add backward compatibility imports in `repository/model.py`

### Phase 3: Implement Loading
1. Create `Project.load_from_file()` in `project/loader.py`
2. Move YAML parsing logic
3. Create `Project.find_and_load()`
4. Add `LoadError` to `project/loader.py`

### Phase 4: Implement Building
1. Move `_build_with_output_groups` to `project/builder.py::build_project()`
2. Implement `Project.build()` wrapper
3. Implement `Project.show_graph()`
4. Implement `Project.clean()`
5. Replace all `click.echo()` with `logger.info()`

### Phase 5: Update CLI
1. Simplify `cli/project.py` to use `Project` API
2. Remove helper functions from `cli/__init__.py`
3. Update imports across codebase

### Phase 6: Update Tests
1. Update unit tests to use new API
2. Add integration tests for `Project` class
3. Test all CLI commands

### Phase 7: Deprecation & Cleanup
1. Add deprecation warnings for old imports
2. Update documentation
3. Remove old code after migration period

## Benefits

### Before
```python
# CLI has 400+ lines of build logic
# Project loading scattered across 3 modules
# Hard to test without CLI
# Mixed concerns (click.echo in business logic)
```

### After
```python
# Clean separation
from gbs.project import Project

project = Project.load_from_file("project.gbs.yaml")
await project.build()

# Or programmatic use
project = Project.load_from_file("test.gbs.yaml")
await project.build(output_dir="test_build", show_progress=False)
assert (Path("test_build") / "output.bin").exists()

# Silent builds
import logging
logging.getLogger("Project").setLevel(logging.WARNING)
await project.build()  # No output unless errors
```

## Compatibility

All existing code continues to work:
```python
# Old way (still works during transition)
from gbs.repository.model import Project  # Deprecated warning
from gbs.repository.loader import load_project

# New way
from gbs.project import Project
project = Project.load_from_file(...)
```

## Files to Modify

**New files**:
- `gbs/project/__init__.py`
- `gbs/project/model.py`
- `gbs/project/loader.py`
- `gbs/project/builder.py`

**Modified files**:
- `gbs/repository/model.py` (remove Project, add compat imports)
- `gbs/repository/loader.py` (remove project loading, add compat imports)
- `gbs/cli/__init__.py` (simplify or remove helpers)
- `gbs/cli/project.py` (reduce to thin wrappers, ~400→70 lines)
- `gbs/cli/repo.py` (update imports if needed)
- Various test files

**Lines of code**:
- **Moved**: ~600 lines
- **New**: ~200 lines (Project class with methods)
- **Deleted**: ~200 lines (duplicate/obsolete code)
- **Net**: Similar total, much better organized

## Questions for Review

1. Should `ProjectModel` be kept as a separate class, or merge into `Project`?
2. Should `builder.py` and `planner.py` be split or combined?
3. Where should `LoadError` live? (project/, repository/, or shared exceptions/)
4. Should we keep backward compat imports forever or deprecate after version X?
5. Timeline for migration - one PR or multiple phases?

## Next Steps

Once approved, implementation order:
1. Create module structure
2. Move models with backward compat
3. Implement `Project.load_from_file()`
4. Implement `Project.build()`
5. Update CLI
6. Test and refine
7. Documentation
