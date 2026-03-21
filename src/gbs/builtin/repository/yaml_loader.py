"""YAML repository loader - Built-in GBS repository format

Provides YAML-based repository loading for .gbs.yaml format.
"""

import yaml
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from ...repository.model import Repository, Partition, SourceFile
from ...repository.loader import RepositoryLoader, LoadError
from ...repository.filters import evaluate_filter
from ...logging import get_logger

logger = get_logger(__name__)


@dataclass
class FilterCondition:
    """Internal: A single conditional branch within a group"""
    expression: str
    deps: list[str] = field(default_factory=list)
    sources: list[SourceFile] = field(default_factory=list)
    groups: list['ConditionalGroup'] = field(default_factory=list)

    def is_default(self) -> bool:
        return self.expression.strip() == "default"


@dataclass
class ConditionalGroup:
    """Internal: A group of mutually exclusive conditions"""
    name: str
    conditions: list[FilterCondition] = field(default_factory=list)


@dataclass
class PartitionTemplate:
    """Internal: Unevaluated partition with conditional logic"""
    name: str
    groups: list[ConditionalGroup] = field(default_factory=list)


@dataclass
class LibrarySpec:
    """Internal: Library specification with partition templates"""
    name: str
    partition_templates: dict[str, PartitionTemplate] = field(default_factory=dict)


class YAMLRepository(Repository):
    """Repository implementation for YAML .gbs.yaml format"""

    def __init__(self, name: str, root: Path, libraries: dict[str, LibrarySpec]):
        """Initialize YAML repository

        Args:
            name: Repository name
            root: Repository root path
            libraries: Dictionary of library name -> LibrarySpec
        """
        super().__init__(name, root)
        self._libraries = libraries

    def file_types(self) -> set[str]:
        """Get all file types in this repository"""
        types = set()
        for lib_spec in self._libraries.values():
            for partition_template in lib_spec.partition_templates.values():
                # Scan all sources in all conditions
                types.update(self._scan_file_types(partition_template.groups))
        return types

    def _scan_file_types(self, groups: list[ConditionalGroup]) -> set[str]:
        """Recursively scan for file types in conditional groups"""
        types = set()
        for group in groups:
            for condition in group.conditions:
                for source in condition.sources:
                    types.add(source.file_type)
                # Recurse into nested groups
                types.update(self._scan_file_types(condition.groups))
        return types

    def partition_lookup(self, partition_name: str, filter_vars: dict[str, str]) -> Optional[Partition]:
        """Lookup partition by name and expand with filter variables

        Args:
            partition_name: Partition name in "library.partition" format
            filter_vars: Filter variables for conditional expansion

        Returns:
            Expanded Partition or None if not found
        """
        # Parse partition name
        if '.' not in partition_name:
            logger.warning(f"Partition name must be in 'library.partition' format: {partition_name}")
            return None

        library_name, partition_only = partition_name.split('.', 1)

        # Find library
        if library_name not in self._libraries:
            return None

        lib_spec = self._libraries[library_name]

        # Find partition template
        if partition_only not in lib_spec.partition_templates:
            return None

        partition_template = lib_spec.partition_templates[partition_only]

        # Expand partition by evaluating conditionals
        sources = []
        deps = set()

        for group in partition_template.groups:
            group_sources, group_deps = self._evaluate_group(group, filter_vars, library_name)
            sources.extend(group_sources)
            deps.update(group_deps)

        return Partition(
            name=partition_name,
            sources=sources,
            deps=deps
        )

    def _evaluate_group(
        self,
        group: ConditionalGroup,
        filter_vars: dict[str, str],
        library_name: str
    ) -> tuple[list[SourceFile], set[str]]:
        """Evaluate a conditional group (first-match wins)

        Args:
            group: Conditional group to evaluate
            filter_vars: Filter variables
            library_name: Current library name (for qualifying deps)

        Returns:
            Tuple of (sources, deps) from first matching condition
        """
        for condition in group.conditions:
            if condition.is_default() or evaluate_filter(condition.expression, filter_vars):
                # Match! Collect sources and deps from this condition
                sources = list(condition.sources)
                deps = set()

                # Qualify dependencies with library name if not already qualified
                for dep in condition.deps:
                    if '.' in dep:
                        deps.add(dep)  # Already qualified
                    else:
                        deps.add(f"{library_name}.{dep}")  # Qualify with current library

                # Recursively evaluate nested groups
                for nested_group in condition.groups:
                    nested_sources, nested_deps = self._evaluate_group(
                        nested_group, filter_vars, library_name
                    )
                    sources.extend(nested_sources)
                    deps.update(nested_deps)

                return sources, deps

        # No condition matched
        return [], set()


class YAMLRepositoryLoader(RepositoryLoader):
    """Repository loader for YAML format (.gbs.yaml files)"""

    def __init__(self, path):
        super().__init__(path)
        self._files_read: list[Path] = []

    def load(self) -> Repository:
        """Load repository from YAML file

        Returns:
            YAMLRepository instance

        Raises:
            LoadError: If repository cannot be loaded
        """
        self._files_read = []
        logger.debug(f"Loading YAML repository from {self.path}")
        data = self._load_yaml_file(self.path)

        if "name" not in data:
            raise LoadError(f"Repository file {self.path} missing 'name' field")

        name = data["name"]
        root = self.path.parent

        # Load libraries
        libraries = {}
        if "libraries" in data:
            for library_spec in data["libraries"]:
                if isinstance(library_spec, dict) and "path" in library_spec:
                    lib_path = root / library_spec["path"]
                    if lib_path.is_dir():
                        for filename in ["library.gbs.yaml", "library.gbs"]:
                            lib_file = lib_path / filename
                            if lib_file.exists():
                                lib_path = lib_file
                                break

                    if lib_path.exists():
                        try:
                            lib = self._load_library(lib_path)
                            libraries[lib.name] = lib
                        except LoadError as e:
                            logger.warning(f"Failed to load library from {lib_path}: {e}")

                elif isinstance(library_spec, dict) and "pattern" in library_spec:
                    pattern = library_spec["pattern"]
                    matches = list(root.glob(pattern))
                    for match_path in matches:
                        try:
                            lib = self._load_library(match_path)
                            libraries[lib.name] = lib
                        except LoadError as e:
                            logger.warning(f"Failed to load library from {match_path}: {e}")

        logger.info(f"Loaded YAML repository '{name}' with {len(libraries)} libraries")
        repo = YAMLRepository(name=name, root=root, libraries=libraries)
        repo.definition_files = list(self._files_read)
        return repo

    def _load_library(self, path: Path) -> LibrarySpec:
        """Load a library from YAML file"""
        logger.debug(f"Loading library from {path}")
        data = self._load_yaml_file(path)

        if "name" not in data:
            raise LoadError(f"Library file {path} missing 'name' field")

        name = data["name"]
        partition_templates = {}

        # Load partitions
        if "partitions" in data:
            base_path = path.parent

            for partition_spec in data["partitions"]:
                if isinstance(partition_spec, str):
                    partition_path = base_path / partition_spec
                    if not partition_path.suffix:
                        partition_path = partition_path.with_suffix(".gbs.yaml")

                    if partition_path.exists():
                        try:
                            partition_template = self._load_partition_template(partition_path)
                            partition_templates[partition_template.name] = partition_template
                        except LoadError as e:
                            logger.warning(f"Failed to load partition from {partition_path}: {e}")

                elif isinstance(partition_spec, dict) and "pattern" in partition_spec:
                    pattern = partition_spec["pattern"]
                    matches = list(base_path.glob(pattern))
                    for match_path in matches:
                        try:
                            partition_template = self._load_partition_template(match_path)
                            partition_templates[partition_template.name] = partition_template
                        except LoadError as e:
                            logger.warning(f"Failed to load partition from {match_path}: {e}")

        logger.info(f"Loaded library '{name}' with {len(partition_templates)} partitions")
        return LibrarySpec(name=name, partition_templates=partition_templates)

    def _load_partition_template(self, path: Path) -> PartitionTemplate:
        """Load a partition template from YAML file"""
        logger.debug(f"Loading partition template from {path}")
        data = self._load_yaml_file(path)

        # Extract partition name from filename
        name = path.stem
        if name.endswith('.gbs'):
            name = name[:-4]

        base_path = path.parent

        # Parse root-level sources and deps
        sources_data = data.get("sources", [])
        sources = self._load_sources(sources_data, base_path)
        deps = data.get("deps", [])

        # Load nested groups
        nested_groups = []
        groups_data = data.get("groups", {})
        for group_name, group_conditions in groups_data.items():
            if not isinstance(group_conditions, list):
                raise LoadError(f"Group '{group_name}' must be a list of conditions")
            nested_group = self._load_conditional_group(group_name, group_conditions, base_path)
            nested_groups.append(nested_group)

        # Create root condition (implicit "default")
        root_condition = FilterCondition(
            expression="default",
            deps=deps,
            sources=sources,
            groups=nested_groups
        )

        root_group = ConditionalGroup(name="root", conditions=[root_condition])

        logger.info(f"Loaded partition template '{name}' from {path}")
        return PartitionTemplate(name=name, groups=[root_group])

    def _load_conditional_group(
        self,
        name: str,
        conditions_data: list[dict],
        base_path: Path
    ) -> ConditionalGroup:
        """Load a conditional group from YAML data"""
        conditions = []

        for cond_data in conditions_data:
            if "condition" not in cond_data:
                raise LoadError(f"Condition in group '{name}' missing 'condition' field")

            expression = cond_data["condition"]
            deps = cond_data.get("deps", [])
            sources_data = cond_data.get("sources", [])
            sources = self._load_sources(sources_data, base_path)

            # Load nested groups (recursive)
            nested_groups = []
            groups_data = cond_data.get("groups", {})
            for group_name, group_conditions in groups_data.items():
                nested_group = self._load_conditional_group(group_name, group_conditions, base_path)
                nested_groups.append(nested_group)

            condition = FilterCondition(
                expression=expression,
                deps=deps,
                sources=sources,
                groups=nested_groups
            )
            conditions.append(condition)

        return ConditionalGroup(name=name, conditions=conditions)

    def _load_sources(self, sources_data: list[dict], base_path: Path) -> list[SourceFile]:
        """Load source files from YAML data"""
        result = []

        for source_spec in sources_data:
            file_type = source_spec.get("file_type") or source_spec.get("language")
            if not file_type:
                raise LoadError("Source specification missing 'file_type' field")
            if "files" not in source_spec:
                raise LoadError("Source specification missing 'files' field")

            variant = source_spec.get("variant")

            for file_path in source_spec["files"]:
                result.append(SourceFile(
                    path=base_path / file_path,
                    file_type=file_type,
                    variant=variant
                ))

        return result

    def _load_yaml_file(self, path: Path) -> dict:
        """Load and parse a YAML file"""
        self._files_read.append(path.resolve())
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


__all__ = ["YAMLRepositoryLoader", "YAMLRepository"]
