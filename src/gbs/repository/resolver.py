"""Dependency resolution with repository partition lookup

This module handles dependency traversal and build order determination.
Dependencies are resolved by querying repositories with filter variables,
creating a DAG of partitions that is topologically sorted for build order.
"""

from dataclasses import dataclass
from collections import deque
from typing import Optional

from .model import Repository, Partition, SourceFileSet
from ..logging import get_logger


logger = get_logger(__name__)


class ResolutionError(Exception):
    """Error during dependency resolution"""
    pass


class CyclicDependencyError(ResolutionError):
    """Cyclic dependency detected"""
    pass


class DependencyResolver:
    """Resolves partition dependencies across repositories

    The resolver:
    1. Starts with root partition(s)
    2. For each dependency, queries all repositories until one returns a match
    3. Builds a DAG of partitions
    4. Topologically sorts for build order
    """

    def __init__(self, repositories: list[Repository], filter_vars: dict[str, str]):
        """Initialize resolver

        Args:
            repositories: List of repositories to search
            filter_vars: Filter variables for partition lookup
        """
        self.repositories = repositories
        self.filter_vars = filter_vars
        self._resolved: dict[str, Partition] = {}  # partition_name -> Partition
        self._resolving: set[str] = set()  # Track partitions being resolved (for cycle detection)

    def resolve(self, root_partitions: list[Partition]) -> SourceFileSet:
        """Resolve dependencies starting from root partitions

        Args:
            root_partitions: Starting partitions (from project root)

        Returns:
            SourceFileSet with partitions in build order

        Raises:
            ResolutionError: If resolution fails
            CyclicDependencyError: If circular dependency detected
        """
        logger.info(f"Resolving dependencies for {len(root_partitions)} root partition(s)")

        # Add root partitions to resolved set
        for partition in root_partitions:
            self._resolved[partition.name] = partition

        # Recursively resolve dependencies
        for partition in root_partitions:
            self._resolve_partition_deps(partition)

        logger.info(f"Resolved {len(self._resolved)} total partitions")

        # Build dependency graph and topologically sort
        sorted_partitions = self._topological_sort()

        # Build SourceFileSet
        result = SourceFileSet()
        for partition in sorted_partitions:
            result.add_partition(partition)

        return result

    def _resolve_partition_deps(self, partition: Partition):
        """Recursively resolve dependencies for a partition

        Args:
            partition: Partition whose dependencies to resolve

        Raises:
            ResolutionError: If dependency cannot be resolved
            CyclicDependencyError: If circular dependency detected
        """
        for dep_name in partition.deps:
            # Already resolved?
            if dep_name in self._resolved:
                continue

            # Currently resolving (cycle detection)
            if dep_name in self._resolving:
                raise CyclicDependencyError(
                    f"Circular dependency detected: {dep_name} is already being resolved"
                )

            # Mark as resolving
            self._resolving.add(dep_name)

            # Lookup partition in repositories
            dep_partition = self._lookup_partition(dep_name)

            if dep_partition is None:
                raise ResolutionError(
                    f"Cannot resolve dependency '{dep_name}' (required by '{partition.name}')"
                )

            # Store resolved partition
            self._resolved[dep_name] = dep_partition

            # Recursively resolve its dependencies
            self._resolve_partition_deps(dep_partition)

            # Mark as done resolving
            self._resolving.remove(dep_name)

    def _lookup_partition(self, partition_name: str) -> Optional[Partition]:
        """Lookup partition in repositories

        Queries each repository until one returns a match.

        Args:
            partition_name: Partition name in "library.partition" format

        Returns:
            Partition if found in any repository, None otherwise
        """
        logger.info(f"Looking up partition: {partition_name} with filter_vars: {self.filter_vars}")
        logger.info(f"Available repositories: {[r.name for r in self.repositories]}")

        for repo in self.repositories:
            logger.info(f"Querying repository '{repo.name}' for partition '{partition_name}'")
            partition = repo.partition_lookup(partition_name, self.filter_vars)
            if partition is not None:
                logger.info(f"Found partition '{partition_name}' in repository '{repo.name}'")
                return partition
            else:
                logger.info(f"Repository '{repo.name}' returned None for '{partition_name}'")

        logger.error(f"Partition '{partition_name}' not found in any repository")
        return None

    def _topological_sort(self) -> list[Partition]:
        """Topologically sort resolved partitions

        Returns:
            List of partitions in build order (dependencies first)

        Raises:
            CyclicDependencyError: If circular dependency detected
        """
        # Build in-degree map
        in_degree = {name: 0 for name in self._resolved}

        for partition in self._resolved.values():
            for dep in partition.deps:
                if dep in in_degree:  # Only count deps we've resolved
                    in_degree[partition.name] += 1

        # Kahn's algorithm
        queue = deque([name for name, degree in in_degree.items() if degree == 0])
        result = []

        while queue:
            # Sort queue for deterministic order
            queue = deque(sorted(queue))
            name = queue.popleft()
            partition = self._resolved[name]
            result.append(partition)

            # Reduce in-degree for dependents
            for other_name, other_partition in self._resolved.items():
                if name in other_partition.deps:
                    in_degree[other_name] -= 1
                    if in_degree[other_name] == 0:
                        queue.append(other_name)

        if len(result) != len(self._resolved):
            raise CyclicDependencyError("Circular dependency detected during topological sort")

        logger.debug(f"Topological sort complete: {len(result)} partitions")
        return result


__all__ = [
    "DependencyResolver",
    "ResolutionError",
    "CyclicDependencyError",
]
