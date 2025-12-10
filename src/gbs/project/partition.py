"""Project partition parsing and evaluation

Project files contain root partition definitions with conditional logic.
These are parsed into templates and evaluated with filter variables when
building each output group.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..repository.model import Partition, SourceFile
from ..repository.filters import evaluate_filter
from ..logging import get_logger

logger = get_logger(__name__)


@dataclass
class FilterCondition:
    """A single conditional branch within a group"""
    expression: str
    deps: list[str] = field(default_factory=list)
    sources: list[SourceFile] = field(default_factory=list)
    groups: list['ConditionalGroup'] = field(default_factory=list)

    def is_default(self) -> bool:
        return self.expression.strip() == "default"


@dataclass
class ConditionalGroup:
    """A group of mutually exclusive conditions"""
    name: str
    conditions: list[FilterCondition] = field(default_factory=list)


@dataclass
class PartitionTemplate:
    """Unevaluated partition with conditional logic

    This is the internal representation of a project's root partition
    before filter variables are applied.
    """
    name: str
    groups: list[ConditionalGroup] = field(default_factory=list)

    def evaluate(self, filter_vars: dict[str, str], library_name: str) -> Partition:
        """Evaluate partition template with filter variables

        Args:
            filter_vars: Filter variables for conditional expansion
            library_name: Library name for qualifying partition name and deps

        Returns:
            Expanded Partition with resolved sources and dependencies
        """
        partition_name = f"{library_name}.{self.name}"

        sources = []
        deps = set()

        for group in self.groups:
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
            library_name: Library name for qualifying deps

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


def parse_partition_template(
    partition_data: dict,
    base_path: Path,
    default_name: str = "root"
) -> PartitionTemplate:
    """Parse partition template from YAML data

    Args:
        partition_data: Partition data dictionary
        base_path: Base path for resolving source file paths
        default_name: Default partition name if not specified

    Returns:
        PartitionTemplate with conditional logic
    """
    partition_name = partition_data.get("name", default_name)

    # Parse root-level sources
    sources_data = partition_data.get("sources", [])
    sources = _parse_sources(sources_data, base_path)

    # Parse root-level deps
    deps = partition_data.get("deps", [])

    # Parse nested groups
    nested_groups = []
    groups_data = partition_data.get("groups", {})
    for group_name, group_conditions in groups_data.items():
        if not isinstance(group_conditions, list):
            raise ValueError(f"Group '{group_name}' must be a list of conditions")
        nested_group = _parse_conditional_group(group_name, group_conditions, base_path)
        nested_groups.append(nested_group)

    # Create root condition (implicit "default")
    root_condition = FilterCondition(
        expression="default",
        deps=deps,
        sources=sources,
        groups=nested_groups
    )

    root_group = ConditionalGroup(name="root", conditions=[root_condition])

    return PartitionTemplate(name=partition_name, groups=[root_group])


def _parse_conditional_group(
    name: str,
    conditions_data: list[dict],
    base_path: Path
) -> ConditionalGroup:
    """Parse a conditional group from YAML data"""
    conditions = []

    for cond_data in conditions_data:
        if "condition" not in cond_data:
            raise ValueError(f"Condition in group '{name}' missing 'condition' field")

        expression = cond_data["condition"]
        deps = cond_data.get("deps", [])
        sources_data = cond_data.get("sources", [])
        sources = _parse_sources(sources_data, base_path)

        # Parse nested groups (recursive)
        nested_groups = []
        groups_data = cond_data.get("groups", {})
        for group_name, group_conditions in groups_data.items():
            nested_group = _parse_conditional_group(group_name, group_conditions, base_path)
            nested_groups.append(nested_group)

        condition = FilterCondition(
            expression=expression,
            deps=deps,
            sources=sources,
            groups=nested_groups
        )
        conditions.append(condition)

    return ConditionalGroup(name=name, conditions=conditions)


def _parse_sources(sources_data: list[dict], base_path: Path) -> list[SourceFile]:
    """Parse source files from YAML data"""
    result = []

    for source_spec in sources_data:
        file_type = source_spec.get("file_type") or source_spec.get("language")
        if not file_type:
            raise ValueError("Source specification missing 'file_type' field")
        if "files" not in source_spec:
            raise ValueError("Source specification missing 'files' field")

        variant = source_spec.get("variant")

        for file_path in source_spec["files"]:
            result.append(SourceFile(
                path=base_path / file_path,
                file_type=file_type,
                variant=variant
            ))

    return result


__all__ = [
    "PartitionTemplate",
    "parse_partition_template",
]
