"""Tests for planner source type seeding from repositories and templates."""

from gbs.planner.planner import BuildPlanner
from gbs.project.partition import (
    ConditionalGroup,
    FilterCondition,
    PartitionTemplate,
)
from gbs.repository.model import Repository


class FakeRepository(Repository):
    def __init__(self):
        super().__init__("fake", None)

    def file_types(self):
        return {"vhdl"}

    def partition_lookup(self, partition_name, filter_vars):
        return None


def template(deps, file_types=()):
    from gbs.repository.model import SourceFile
    sources = [SourceFile(path=None, file_type=t) for t in file_types]
    return PartitionTemplate(
        name="top",
        groups=[ConditionalGroup(
            name="main",
            conditions=[FilterCondition(
                expression="default", deps=deps, sources=sources)],
        )],
    )


def test_has_deps():
    assert template(["lib.part"]).has_deps()
    assert not template([]).has_deps()


def test_has_deps_nested():
    nested = ConditionalGroup(
        name="inner",
        conditions=[FilterCondition(expression="default",
                                    deps=["lib.part"])],
    )
    t = PartitionTemplate(
        name="top",
        groups=[ConditionalGroup(
            name="main",
            conditions=[FilterCondition(expression="default",
                                        groups=[nested])],
        )],
    )
    assert t.has_deps()


def test_repository_types_included_with_deps():
    planner = BuildPlanner(
        repositories=[FakeRepository()],
        backends=[],
        root_partition_template=template(["lib.part"], ["vivado-bus-yaml"]),
    )
    assert planner.available_source_types == {"vhdl", "vivado-bus-yaml"}


def test_repository_types_excluded_without_deps():
    planner = BuildPlanner(
        repositories=[FakeRepository()],
        backends=[],
        root_partition_template=template([], ["vivado-bus-yaml"]),
    )
    assert planner.available_source_types == {"vivado-bus-yaml"}


def test_repository_types_included_without_template():
    planner = BuildPlanner(
        repositories=[FakeRepository()],
        backends=[],
    )
    assert planner.available_source_types == {"vhdl"}
