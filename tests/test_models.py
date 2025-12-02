"""Tests for GBS core data models"""

from pathlib import Path
import pytest

from gbs.models import (
    SourceFile,
    FilterCondition,
    ConditionalGroup,
    Partition,
    Library,
    Repository,
    Project,
    SourceFileSet,
    OutputFile,
    OutputGroup,
)


def test_source_file():
    """Test SourceFile creation and string representation"""
    sf = SourceFile(Path("test.vhd"), "vhdl")
    assert sf.path == Path("test.vhd")
    assert sf.file_type == "vhdl"
    assert sf.variant is None
    assert "test.vhd" in str(sf)
    assert "vhdl" in str(sf)

    sf_variant = SourceFile(Path("test.vhd"), "vhdl", variant="2008")
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
    """Test SourceFileSet creation and management"""
    bfs = SourceFileSet()
    assert len(bfs.libraries) == 0
    assert len(bfs.get_all_files()) == 0

    # Add files to a partition
    files1 = [
        SourceFile(Path("file1.vhd"), "vhdl"),
        SourceFile(Path("file2.vhd"), "vhdl"),
    ]
    bfs.add_partition("lib1", "part1", files1)

    assert "lib1" in bfs.libraries
    assert "part1" in bfs.partitions["lib1"]
    assert len(bfs.get_all_files()) == 2

    # Add more partitions
    files2 = [SourceFile(Path("file3.vhd"), "vhdl")]
    bfs.add_partition("lib1", "part2", files2)

    files3 = [SourceFile(Path("file4.v"), "verilog")]
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
        sources=[SourceFile(Path("xilinx.vhd"), "vhdl")],
        groups=[nested_group]
    )

    assert len(parent_condition.groups) == 1
    assert parent_condition.groups[0].name == "family_select"
    assert len(parent_condition.groups[0].conditions) == 2


def test_output_file():
    """Test OutputFile creation and string representation"""
    output = OutputFile(type="simulator", path=Path("build/sim.exe"))
    assert output.type == "simulator"
    assert output.path == Path("build/sim.exe")
    assert "simulator" in str(output)
    assert "sim.exe" in str(output)


def test_output_group():
    """Test OutputGroup creation"""
    output_group = OutputGroup(
        name="simulation",
        topcell="testbench",
        filter_vars={"sim": 1, "vendor": "generic"},
        backend_config={"gbs.backend.ghdl": {"vhdl_standard": "2008"}},
        outputs=[
            OutputFile(type="simulator", path=Path("build/sim.exe"))
        ]
    )

    assert output_group.name == "simulation"
    assert output_group.topcell == "testbench"
    assert output_group.filter_vars["sim"] == 1
    assert "gbs.backend.ghdl" in output_group.backend_config
    assert len(output_group.outputs) == 1
    assert output_group.outputs[0].type == "simulator"
    assert "simulation" in str(output_group)


def test_output_group_with_constraints():
    """Test OutputGroup with build planning constraints"""
    output_group = OutputGroup(
        name="synthesis",
        topcell="top",
        filter_vars={"vendor": "gowin"},
        require_backends=["gbs.backend.gowin"],
        exclude_passes=["gbs.backend.ghdl:simulate"],
        outputs=[
            OutputFile(type="gowin-fs", path=Path("build/bitstream.fs")),
            OutputFile(type="gowin-bin", path=Path("build/flash.bin"))
        ]
    )

    assert output_group.name == "synthesis"
    assert len(output_group.require_backends) == 1
    assert "gbs.backend.gowin" in output_group.require_backends
    assert len(output_group.exclude_passes) == 1
    assert len(output_group.outputs) == 2


def test_project_with_output_groups():
    """Test Project with output_groups field"""
    root_partition = Partition(name="test_partition", groups=[])

    output_group = OutputGroup(
        name="sim",
        topcell="tb",
        outputs=[OutputFile(type="simulator", path=Path("sim.exe"))]
    )

    project = Project(
        name="test_project",
        root_partition=root_partition,
        topcell="top",
        filter_vars={"vendor": "xilinx"},
        output_groups=[output_group]
    )

    assert project.name == "test_project"
    assert len(project.output_groups) == 1
    assert project.output_groups[0].name == "sim"
    assert project.output_groups[0].topcell == "tb"
