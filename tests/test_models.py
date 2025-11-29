"""Tests for GBS core data models"""

from pathlib import Path
import pytest

from gbs.models import (
    SourceFile,
    Language,
    FilterCondition,
    ConditionalGroup,
    Partition,
    Library,
    Repository,
    Project,
    BuildFileSet,
)


def test_source_file():
    """Test SourceFile creation and string representation"""
    sf = SourceFile(Path("test.vhd"), Language.VHDL)
    assert sf.path == Path("test.vhd")
    assert sf.language == Language.VHDL
    assert sf.variant is None
    assert "test.vhd" in str(sf)
    assert "vhdl" in str(sf)

    sf_variant = SourceFile(Path("test.vhd"), Language.VHDL, variant="2008")
    assert sf_variant.variant == "2008"
    assert "2008" in str(sf_variant)


def test_filter_condition():
    """Test FilterCondition with default checking"""
    cond = FilterCondition("vendor = \"xilinx\"")
    assert not cond.is_default()
    assert cond.expression == "vendor = \"xilinx\""
    assert cond.deps == []
    assert cond.sources == []
    assert cond.groups == []

    default_cond = FilterCondition("default")
    assert default_cond.is_default()


def test_conditional_group():
    """Test ConditionalGroup with multiple conditions"""
    group = ConditionalGroup(
        name="vendor_select",
        conditions=[
            FilterCondition("vendor = \"xilinx\""),
            FilterCondition("vendor = \"intel\""),
            FilterCondition("default"),
        ]
    )
    assert group.name == "vendor_select"
    assert len(group.conditions) == 3
    assert "vendor_select" in str(group)


def test_partition():
    """Test Partition with groups"""
    partition = Partition(
        name="test_partition",
        groups=[
            ConditionalGroup(
                name="group1",
                conditions=[FilterCondition("default")]
            )
        ]
    )
    assert partition.name == "test_partition"
    assert len(partition.groups) == 1


def test_library():
    """Test Library with partition management"""
    lib = Library(name="test_lib")
    assert lib.name == "test_lib"
    assert len(lib.partitions) == 0

    p1 = Partition(name="part1")
    p2 = Partition(name="part2")

    lib.add_partition(p1)
    lib.add_partition(p2)

    assert len(lib.partitions) == 2
    assert lib.get_partition("part1") == p1
    assert lib.get_partition("part2") == p2
    assert lib.get_partition("nonexistent") is None


def test_repository():
    """Test Repository with library management"""
    repo = Repository(name="test_repo", root=Path("/test"))
    assert repo.name == "test_repo"
    assert repo.root == Path("/test")
    assert len(repo.libraries) == 0

    lib1 = Library(name="lib1")
    lib2 = Library(name="lib2")

    repo.add_library(lib1)
    repo.add_library(lib2)

    assert len(repo.libraries) == 2
    assert repo.get_library("lib1") == lib1
    assert repo.get_library("lib2") == lib2
    assert repo.get_library("nonexistent") is None


def test_project():
    """Test Project definition"""
    root_partition = Partition(name="test_partition", groups=[])

    project = Project(
        name="test_project",
        root_partition=root_partition,
        topcell="top",
        filter_vars={"vendor": "xilinx", "family": "7series"}
    )

    assert project.name == "test_project"
    assert project.topcell == "top"
    assert project.filter_vars["vendor"] == "xilinx"
    assert "test_project" in str(project)


def test_build_file_set():
    """Test BuildFileSet creation and management"""
    bfs = BuildFileSet()
    assert len(bfs.libraries) == 0
    assert len(bfs.get_all_files()) == 0

    # Add files to a partition
    files1 = [
        SourceFile(Path("file1.vhd"), Language.VHDL),
        SourceFile(Path("file2.vhd"), Language.VHDL),
    ]
    bfs.add_partition("lib1", "part1", files1)

    assert "lib1" in bfs.libraries
    assert "part1" in bfs.partitions["lib1"]
    assert len(bfs.get_all_files()) == 2

    # Add more partitions
    files2 = [SourceFile(Path("file3.vhd"), Language.VHDL)]
    bfs.add_partition("lib1", "part2", files2)

    files3 = [SourceFile(Path("file4.v"), Language.VERILOG)]
    bfs.add_partition("lib2", "part1", files3)

    assert len(bfs.libraries) == 2
    assert len(bfs.get_all_files()) == 4


def test_recursive_conditional_groups():
    """Test nested conditional groups"""
    nested_group = ConditionalGroup(
        name="family_select",
        conditions=[
            FilterCondition("family = \"7series\""),
            FilterCondition("default"),
        ]
    )

    parent_condition = FilterCondition(
        expression="vendor = \"xilinx\"",
        deps=["xilinx_lib.primitives"],
        sources=[SourceFile(Path("xilinx.vhd"), Language.VHDL)],
        groups=[nested_group]
    )

    assert len(parent_condition.groups) == 1
    assert parent_condition.groups[0].name == "family_select"
    assert len(parent_condition.groups[0].conditions) == 2
