"""Dependency resolution with filter evaluation

This module handles dependency traversal, filter evaluation, and build order
determination. Dependencies are resolved with filter context, creating a DAG
of partitions that is topologically sorted for build order.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from collections import defaultdict, deque

from .model import (
    Partition,
    Library,
    Repository,
    Project,
    SourceFileSet,
    SourceFile,
    ConditionalGroup,
    FilterCondition,
)
from .filters import evaluate_filter
from ..logging import get_logger


logger = get_logger(__name__)


class ResolutionError(Exception):
    """Error during dependency resolution"""
    pass


class CyclicDependencyError(ResolutionError):
    """Cyclic dependency detected"""
    pass


@dataclass
class PartitionRef:
    """Reference to a partition (library.partition format)"""
    library: str
    partition: str

    @classmethod
    def parse(cls, ref_string: str) -> 'PartitionRef':
        """Parse a partition reference string

        Args:
            ref_string: Reference in format "library.partition"

        Returns:
            PartitionRef object

        Raises:
            ValueError: If format is invalid
        """
        parts = ref_string.split('.')
        if len(parts) != 2:
            raise ValueError(
                f"Invalid partition reference '{ref_string}': "
                f"expected 'library.partition' format"
            )
        return cls(library=parts[0], partition=parts[1])

    def __str__(self) -> str:
        return f"{self.library}.{self.partition}"

    def __hash__(self) -> int:
        return hash((self.library, self.partition))


@dataclass
class ResolvedPartition:
    """A partition with its resolved dependencies and sources"""
    ref: PartitionRef
    partition: Partition
    sources: list[SourceFile] = field(default_factory=list)
    deps: list[PartitionRef] = field(default_factory=list)


class DependencyResolver:
    """Resolves partition dependencies with filter evaluation"""

    def __init__(
        self,
        project: Project,
        repositories: list[Repository],
        filter_vars: dict[str, str | int] | None = None,
    ):
        """Initialize the resolver

        Args:
            project: Project to build
            repositories: List of available repositories
            filter_vars: Filter variables for conditional source selection (defaults to empty dict)
        """
        self.project = project
        self.repositories = repositories
        self.filter_context = filter_vars if filter_vars is not None else {}

        # Build library index: library_name -> Library
        self.libraries: dict[str, Library] = {}

        # Create root library with the single root partition
        # The root library is always named "work" (required by synthesis tools)
        root_library = Library(name=project.root_library_name)
        root_library.add_partition(project.root_partition)
        self.libraries[project.root_library_name] = root_library

        # Index all repository libraries
        for repo in repositories:
            for lib_name, lib in repo.libraries.items():
                if lib_name in self.libraries:
                    logger.warning(
                        f"Library '{lib_name}' defined in multiple repositories, "
                        f"using first occurrence"
                    )
                else:
                    self.libraries[lib_name] = lib

        logger.info(f"Indexed {len(self.libraries)} libraries")

    def get_partition(self, ref: PartitionRef) -> Optional[Partition]:
        """Get a partition by reference

        Args:
            ref: Partition reference

        Returns:
            Partition object, or None if not found
        """
        library = self.libraries.get(ref.library)
        if library is None:
            return None
        return library.get_partition(ref.partition)

    def evaluate_condition(
        self,
        condition: FilterCondition,
        context: dict[str, str | int]
    ) -> tuple[list[SourceFile], list[str], list[ConditionalGroup]]:
        """Evaluate a filter condition

        Args:
            condition: Filter condition to evaluate
            context: Filter variable context

        Returns:
            Tuple of (sources, deps, nested_groups) if condition matches,
            or ([], [], []) if it doesn't match
        """
        # Evaluate the condition expression
        if not evaluate_filter(condition.expression, context):
            return ([], [], [])

        return (
            condition.sources.copy(),
            condition.deps.copy(),
            condition.groups.copy()
        )

    def evaluate_group(
        self,
        group: ConditionalGroup,
        context: dict[str, str | int]
    ) -> tuple[list[SourceFile], list[str]]:
        """Evaluate a conditional group (first-match wins)

        Args:
            group: Conditional group to evaluate
            context: Filter variable context

        Returns:
            Tuple of (sources, deps) from first matching condition
        """
        for condition in group.conditions:
            sources, deps, nested_groups = self.evaluate_condition(condition, context)

            if sources or deps or nested_groups:
                # This condition matched
                all_sources = sources
                all_deps = deps

                # Recursively evaluate nested groups
                for nested_group in nested_groups:
                    nested_sources, nested_deps = self.evaluate_group(nested_group, context)
                    all_sources.extend(nested_sources)
                    all_deps.extend(nested_deps)

                return (all_sources, all_deps)

        # No condition matched
        return ([], [])

    def resolve_partition(self, ref: PartitionRef) -> ResolvedPartition:
        """Resolve a single partition with filter evaluation

        Args:
            ref: Partition reference to resolve

        Returns:
            ResolvedPartition with evaluated sources and dependencies

        Raises:
            ResolutionError: If partition cannot be found or resolved
        """
        logger.debug(f"Resolving partition {ref}")

        partition = self.get_partition(ref)
        if partition is None:
            raise ResolutionError(
                f"Partition '{ref}' not found. "
                f"Available libraries: {list(self.libraries.keys())}"
            )

        # Check if partition supports lazy evaluation (e.g., NSL partitions)
        # Call evaluate_with_context() if available to trigger lazy evaluation
        if hasattr(partition, 'evaluate_with_context'):
            logger.debug(f"Triggering lazy evaluation for {ref} with context {self.filter_context}")
            partition.evaluate_with_context(self.filter_context)

        all_sources = []
        all_deps = []

        # Evaluate all groups in the partition
        for group in partition.groups:
            sources, deps = self.evaluate_group(group, self.filter_context)
            all_sources.extend(sources)
            all_deps.extend(deps)

        # Parse dependency references
        dep_refs = [PartitionRef.parse(dep) for dep in all_deps]

        logger.debug(
            f"Resolved {ref}: {len(all_sources)} sources, {len(dep_refs)} deps"
        )

        return ResolvedPartition(
            ref=ref,
            partition=partition,
            sources=all_sources,
            deps=dep_refs
        )

    def build_dependency_graph(
        self,
        start_partitions: list[PartitionRef]
    ) -> dict[PartitionRef, ResolvedPartition]:
        """Build dependency graph starting from root partitions

        Args:
            start_partitions: List of root partition references

        Returns:
            Dictionary mapping partition refs to resolved partitions

        Raises:
            ResolutionError: If resolution fails
        """
        logger.info(f"Building dependency graph from {len(start_partitions)} roots")

        resolved: dict[PartitionRef, ResolvedPartition] = {}
        to_process = deque(start_partitions)
        in_progress = set()

        while to_process:
            ref = to_process.popleft()

            # Skip if already resolved
            if ref in resolved:
                continue

            # Check for cycles
            if ref in in_progress:
                raise CyclicDependencyError(
                    f"Cyclic dependency detected involving '{ref}'"
                )

            in_progress.add(ref)

            # Resolve this partition
            resolved_partition = self.resolve_partition(ref)
            resolved[ref] = resolved_partition

            # Add dependencies to process queue
            for dep_ref in resolved_partition.deps:
                if dep_ref not in resolved:
                    to_process.append(dep_ref)

            in_progress.remove(ref)

        logger.info(f"Dependency graph complete: {len(resolved)} partitions")
        return resolved

    def topological_sort(
        self,
        graph: dict[PartitionRef, ResolvedPartition]
    ) -> list[PartitionRef]:
        """Topologically sort partitions by dependencies

        Args:
            graph: Dependency graph

        Returns:
            List of partition refs in build order (dependencies first)

        Raises:
            CyclicDependencyError: If graph contains cycles
        """
        logger.debug("Performing topological sort")

        # Calculate in-degrees (number of dependencies)
        in_degree = {ref: 0 for ref in graph}
        for resolved in graph.values():
            for dep_ref in resolved.deps:
                if dep_ref in in_degree:
                    in_degree[dep_ref] += 1

        # Start with nodes that have no incoming edges (no dependents)
        # These are the leaves of the dependency tree
        queue = deque([ref for ref, degree in in_degree.items() if degree == 0])
        result = []

        while queue:
            ref = queue.popleft()
            result.append(ref)

            # For each dependency of this node
            resolved = graph[ref]
            for dep_ref in resolved.deps:
                if dep_ref not in in_degree:
                    # External dependency not in graph
                    continue

                # Remove edge by decrementing in-degree
                in_degree[dep_ref] -= 1

                # If no more incoming edges, add to queue
                if in_degree[dep_ref] == 0:
                    queue.append(dep_ref)

        # If we didn't process all nodes, there's a cycle
        if len(result) != len(graph):
            remaining = set(graph.keys()) - set(result)
            raise CyclicDependencyError(
                f"Cyclic dependency detected. Unresolved partitions: {remaining}"
            )

        # Reverse to get dependencies-first order
        result.reverse()

        logger.debug(f"Topological sort complete: {len(result)} partitions")
        return result

    def resolve(self) -> SourceFileSet:
        """Resolve all dependencies and create build file set

        Returns:
            SourceFileSet with ordered partitions and files

        Raises:
            ResolutionError: If resolution fails
        """
        logger.info(f"Resolving dependencies for project '{self.project.name}'")

        # Start from project root partition (in "work" library)
        start_refs = [
            PartitionRef(self.project.root_library_name, self.project.root_partition.name)
        ]

        # Build dependency graph
        graph = self.build_dependency_graph(start_refs)

        # Topologically sort
        sorted_refs = self.topological_sort(graph)

        # Build file set
        build_set = SourceFileSet()

        for ref in sorted_refs:
            resolved = graph[ref]
            # Convert PartitionRef deps to (library, partition) tuples
            deps = [(dep.library, dep.partition) for dep in resolved.deps]
            build_set.add_partition(
                library=ref.library,
                partition=ref.partition,
                files=resolved.sources,
                deps=deps
            )

        logger.info(
            f"Resolution complete: {len(build_set.libraries)} libraries, "
            f"{len(build_set.get_all_files())} files"
        )

        return build_set


def resolve_project(
    project: Project,
    repositories: list[Repository]
) -> SourceFileSet:
    """Resolve project dependencies and create build file set

    Args:
        project: Project to resolve
        repositories: Available repositories

    Returns:
        SourceFileSet with ordered partitions and files

    Raises:
        ResolutionError: If resolution fails
    """
    resolver = DependencyResolver(project, repositories)
    return resolver.resolve()
