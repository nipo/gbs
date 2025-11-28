"""Tests for dependency resolution"""

import pytest
from pathlib import Path

from gbs.resolver import (
    DependencyResolver,
    PartitionRef,
    ResolvedPartition,
    ResolutionError,
    CyclicDependencyError,
    resolve_project,
)
from gbs.models import (
    Library,
    Partition,
    Repository,
    Project,
    FilterCondition,
    ConditionalGroup,
    SourceFile,
    Language,
)


class TestPartitionRef:
    """Tests for PartitionRef"""

    def test_parse_valid_ref(self):
        """Test parsing valid partition reference"""
        ref = PartitionRef.parse("my_lib.my_partition")
        assert ref.library == "my_lib"
        assert ref.partition == "my_partition"

    def test_parse_invalid_ref_no_dot(self):
        """Test parsing invalid reference (no dot)"""
        with pytest.raises(ValueError, match="Invalid partition reference"):
            PartitionRef.parse("invalid")

    def test_parse_invalid_ref_too_many_dots(self):
        """Test parsing invalid reference (too many dots)"""
        with pytest.raises(ValueError, match="Invalid partition reference"):
            PartitionRef.parse("lib.partition.extra")

    def test_str_representation(self):
        """Test string representation"""
        ref = PartitionRef("mylib", "mypart")
        assert str(ref) == "mylib.mypart"

    def test_hash_and_equality(self):
        """Test that refs can be used in sets/dicts"""
        ref1 = PartitionRef("lib", "part")
        ref2 = PartitionRef("lib", "part")
        ref3 = PartitionRef("lib", "other")

        assert ref1 == ref2
        assert ref1 != ref3
        assert len({ref1, ref2, ref3}) == 2  # ref1 and ref2 are same


class TestDependencyResolver:
    """Tests for DependencyResolver"""

    def create_simple_project(self) -> tuple[Project, list[Repository]]:
        """Create a simple project with linear dependencies

        Structure:
            project.top -> lib1.part1 -> lib2.part2
        """
        # Create lib2.part2 (no dependencies)
        part2 = Partition(
            name="part2",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("lib2_file.vhd"), Language.VHDL)],
                    deps=[]
                )]
            )]
        )
        lib2 = Library(name="lib2")
        lib2.add_partition(part2)

        # Create lib1.part1 (depends on lib2.part2)
        part1 = Partition(
            name="part1",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("lib1_file.vhd"), Language.VHDL)],
                    deps=["lib2.part2"]
                )]
            )]
        )
        lib1 = Library(name="lib1")
        lib1.add_partition(part1)

        # Create repository
        repo = Repository(name="test_repo", root=Path("/test"))
        repo.add_library(lib1)
        repo.add_library(lib2)

        # Create project root partition
        root_part = Partition(
            name="top",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("top.vhd"), Language.VHDL)],
                    deps=["lib1.part1"]
                )]
            )]
        )
        root_lib = Library(name="project")
        root_lib.add_partition(root_part)

        project = Project(
            name="test_project",
            root_library=root_lib,
            topcell="top",
            output_format="test",
            filter_vars={}
        )

        return project, [repo]

    def test_simple_linear_dependency(self):
        """Test resolving simple linear dependencies"""
        project, repos = self.create_simple_project()
        resolver = DependencyResolver(project, repos)

        build_set = resolver.resolve()

        # Should have 3 libraries: project, lib1, lib2
        assert len(build_set.libraries) == 3

        # Check order (dependencies first)
        assert "lib2" in build_set.libraries
        assert "lib1" in build_set.libraries
        assert "project" in build_set.libraries

        # lib2 should come before lib1
        lib2_idx = build_set.libraries.index("lib2")
        lib1_idx = build_set.libraries.index("lib1")
        project_idx = build_set.libraries.index("project")

        assert lib2_idx < lib1_idx < project_idx

        # Check all files present
        all_files = build_set.get_all_files()
        assert len(all_files) == 3
        file_names = [f.path.name for f in all_files]
        assert "lib2_file.vhd" in file_names
        assert "lib1_file.vhd" in file_names
        assert "top.vhd" in file_names

    def test_diamond_dependency(self):
        """Test diamond dependency: A->B, A->C, B->D, C->D"""
        # Create D (no deps)
        partD = Partition(
            name="partD",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("D.vhd"), Language.VHDL)]
                )]
            )]
        )
        libD = Library(name="libD")
        libD.add_partition(partD)

        # Create C (depends on D)
        partC = Partition(
            name="partC",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("C.vhd"), Language.VHDL)],
                    deps=["libD.partD"]
                )]
            )]
        )
        libC = Library(name="libC")
        libC.add_partition(partC)

        # Create B (depends on D)
        partB = Partition(
            name="partB",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("B.vhd"), Language.VHDL)],
                    deps=["libD.partD"]
                )]
            )]
        )
        libB = Library(name="libB")
        libB.add_partition(partB)

        # Create A (depends on B and C)
        partA = Partition(
            name="partA",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("A.vhd"), Language.VHDL)],
                    deps=["libB.partB", "libC.partC"]
                )]
            )]
        )
        libA = Library(name="libA")
        libA.add_partition(partA)

        repo = Repository(name="test", root=Path("/test"))
        repo.add_library(libA)
        repo.add_library(libB)
        repo.add_library(libC)
        repo.add_library(libD)

        # Project references partA from libA (not adding it to root)
        root_part = Partition(
            name="root",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    deps=["libA.partA"]
                )]
            )]
        )
        root_lib = Library(name="project")
        root_lib.add_partition(root_part)

        project = Project(
            name="test",
            root_library=root_lib,
            topcell="top",
            output_format="test"
        )

        resolver = DependencyResolver(project, [repo])
        build_set = resolver.resolve()

        # Should have all 5 libraries (project + 4 from repo)
        assert len(build_set.libraries) == 5

        # D should come first (no dependencies)
        assert build_set.libraries[0] == "libD"

        # B and C should come after D but before A
        lib_names = build_set.libraries
        libD_idx = lib_names.index("libD")
        libB_idx = lib_names.index("libB")
        libC_idx = lib_names.index("libC")
        libA_idx = lib_names.index("libA")

        assert libD_idx < libB_idx
        assert libD_idx < libC_idx
        assert libB_idx < libA_idx
        assert libC_idx < libA_idx

    def test_cyclic_dependency_detection(self):
        """Test detection of cyclic dependencies"""
        # Create A -> B -> C -> A cycle
        partA = Partition(
            name="partA",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    deps=["libB.partB"]
                )]
            )]
        )
        libA = Library(name="libA")
        libA.add_partition(partA)

        partB = Partition(
            name="partB",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    deps=["libC.partC"]
                )]
            )]
        )
        libB = Library(name="libB")
        libB.add_partition(partB)

        partC = Partition(
            name="partC",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    deps=["libA.partA"]
                )]
            )]
        )
        libC = Library(name="libC")
        libC.add_partition(partC)

        repo = Repository(name="test", root=Path("/test"))
        repo.add_library(libA)
        repo.add_library(libB)
        repo.add_library(libC)

        root_lib = Library(name="project")
        root_lib.add_partition(partA)

        project = Project(
            name="test",
            root_library=root_lib,
            topcell="top",
            output_format="test"
        )

        resolver = DependencyResolver(project, [repo])

        with pytest.raises(CyclicDependencyError, match="Cyclic dependency"):
            resolver.resolve()

    def test_missing_partition_error(self):
        """Test error when referenced partition doesn't exist"""
        part = Partition(
            name="part",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    deps=["nonexistent_lib.nonexistent_part"]
                )]
            )]
        )
        lib = Library(name="lib")
        lib.add_partition(part)

        root_lib = Library(name="project")
        root_lib.add_partition(part)

        project = Project(
            name="test",
            root_library=root_lib,
            topcell="top",
            output_format="test"
        )

        resolver = DependencyResolver(project, [])

        with pytest.raises(ResolutionError, match="not found"):
            resolver.resolve()

    def test_conditional_dependency_resolution(self):
        """Test filter-based conditional dependencies"""
        # Create two partitions: xilinx and intel
        xilinx_part = Partition(
            name="xilinx",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("xilinx.vhd"), Language.VHDL)]
                )]
            )]
        )
        xilinx_lib = Library(name="vendor_lib")
        xilinx_lib.add_partition(xilinx_part)

        intel_part = Partition(
            name="intel",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("intel.vhd"), Language.VHDL)]
                )]
            )]
        )
        intel_lib = Library(name="vendor_lib")
        intel_lib.add_partition(intel_part)

        # Create partition with conditional dependency
        conditional_part = Partition(
            name="top",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("top.vhd"), Language.VHDL)],
                    groups=[ConditionalGroup(
                        name="vendor",
                        conditions=[
                            FilterCondition(
                                expression='vendor = "xilinx"',
                                deps=["vendor_lib.xilinx"]
                            ),
                            FilterCondition(
                                expression='vendor = "intel"',
                                deps=["vendor_lib.intel"]
                            ),
                        ]
                    )]
                )]
            )]
        )

        lib = Library(name="vendor_lib")
        lib.add_partition(xilinx_part)
        lib.add_partition(intel_part)

        repo = Repository(name="test", root=Path("/test"))
        repo.add_library(lib)

        root_lib = Library(name="project")
        root_lib.add_partition(conditional_part)

        # Test with vendor=xilinx
        project_xilinx = Project(
            name="test",
            root_library=root_lib,
            topcell="top",
            output_format="test",
            filter_vars={"vendor": "xilinx"}
        )

        resolver = DependencyResolver(project_xilinx, [repo])
        build_set = resolver.resolve()

        # Should include xilinx partition
        files = build_set.get_all_files()
        file_names = [f.path.name for f in files]
        assert "xilinx.vhd" in file_names
        assert "intel.vhd" not in file_names

        # Test with vendor=intel
        project_intel = Project(
            name="test",
            root_library=root_lib,
            topcell="top",
            output_format="test",
            filter_vars={"vendor": "intel"}
        )

        resolver = DependencyResolver(project_intel, [repo])
        build_set = resolver.resolve()

        # Should include intel partition
        files = build_set.get_all_files()
        file_names = [f.path.name for f in files]
        assert "intel.vhd" in file_names
        assert "xilinx.vhd" not in file_names

    def test_no_root_partitions_error(self):
        """Test error when project has no root partitions"""
        root_lib = Library(name="project")  # Empty library

        project = Project(
            name="test",
            root_library=root_lib,
            topcell="top",
            output_format="test"
        )

        resolver = DependencyResolver(project, [])

        with pytest.raises(ResolutionError, match="no root partitions"):
            resolver.resolve()

    def test_library_name_conflict(self):
        """Test behavior when library appears in multiple repos (uses first)"""
        # Create two libraries with same name but different partitions
        part1 = Partition(
            name="part1",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("from_repo1.vhd"), Language.VHDL)]
                )]
            )]
        )
        lib1 = Library(name="common")
        lib1.add_partition(part1)
        repo1 = Repository(name="repo1", root=Path("/repo1"))
        repo1.add_library(lib1)

        part2 = Partition(
            name="part2",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("from_repo2.vhd"), Language.VHDL)]
                )]
            )]
        )
        lib2 = Library(name="common")
        lib2.add_partition(part2)
        repo2 = Repository(name="repo2", root=Path("/repo2"))
        repo2.add_library(lib2)

        # Create root partition that depends on common.part1
        root_part = Partition(
            name="root",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    deps=["common.part1"]
                )]
            )]
        )
        root_lib = Library(name="project")
        root_lib.add_partition(root_part)

        project = Project(
            name="test",
            root_library=root_lib,
            topcell="top",
            output_format="test"
        )

        # Should use first occurrence (repo1)
        resolver = DependencyResolver(project, [repo1, repo2])

        # Verify it uses lib1 from repo1 (has part1)
        assert resolver.libraries["common"] == lib1
        assert "part1" in resolver.libraries["common"].partitions


class TestResolveProjectFunction:
    """Tests for the resolve_project convenience function"""

    def test_resolve_project_simple(self):
        """Test resolve_project function"""
        part = Partition(
            name="part",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(Path("file.vhd"), Language.VHDL)]
                )]
            )]
        )
        root_lib = Library(name="project")
        root_lib.add_partition(part)

        project = Project(
            name="test",
            root_library=root_lib,
            topcell="top",
            output_format="test"
        )

        build_set = resolve_project(project, [])

        assert len(build_set.libraries) == 1
        assert len(build_set.get_all_files()) == 1
