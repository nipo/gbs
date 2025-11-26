"""Tests for YAML loaders"""

import pytest
from pathlib import Path

from gbs.loaders import (
    load_partition,
    load_library,
    load_repository,
    load_project,
    LoadError,
)
from gbs.models import Language


# Test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestPartitionLoader:
    """Tests for partition loading"""

    def test_load_simple_partition(self):
        """Test loading a simple partition"""
        partition_file = FIXTURES_DIR / "partitions" / "simple.gbs.yaml"
        partition = load_partition(partition_file)

        # Name comes from filename (simple.gbs.yaml -> simple)
        assert partition.name == "simple"
        assert len(partition.groups) == 1

        # Check root group
        root_group = partition.groups[0]
        assert root_group.name == "root"
        assert len(root_group.conditions) == 1

        # Check root condition (implicit default)
        root_cond = root_group.conditions[0]
        assert root_cond.expression == "default"
        assert root_cond.is_default()
        assert len(root_cond.sources) == 2
        assert root_cond.sources[0].language == Language.VHDL
        assert root_cond.sources[0].path.name == "file1.vhd"

    def test_load_conditional_partition(self):
        """Test loading a partition with conditional groups"""
        partition_file = FIXTURES_DIR / "partitions" / "conditional.gbs.yaml"
        partition = load_partition(partition_file)

        # Name comes from filename
        assert partition.name == "conditional"
        assert len(partition.groups) == 1

        # Get root group
        root_group = partition.groups[0]
        assert root_group.name == "root"
        assert len(root_group.conditions) == 1

        # Get root condition
        root_cond = root_group.conditions[0]
        assert root_cond.is_default()
        assert "some_lib.base" in root_cond.deps
        assert len(root_cond.sources) == 1

        # Check nested vendor_selection group
        assert len(root_cond.groups) == 1
        vendor_group = root_cond.groups[0]
        assert vendor_group.name == "vendor_selection"
        assert len(vendor_group.conditions) == 3

        # Check xilinx condition
        xilinx_cond = vendor_group.conditions[0]
        assert xilinx_cond.expression == 'vendor = "xilinx"'
        assert "xilinx_lib.primitives" in xilinx_cond.deps
        assert len(xilinx_cond.sources) == 1

        # Check nested family_selection group
        assert len(xilinx_cond.groups) == 1
        family_group = xilinx_cond.groups[0]
        assert family_group.name == "family_selection"
        assert len(family_group.conditions) == 2

    def test_load_nonexistent_partition(self):
        """Test error when loading nonexistent partition"""
        with pytest.raises(LoadError, match="File not found"):
            load_partition(FIXTURES_DIR / "partitions" / "nonexistent.gbs.yaml")

    def test_load_empty_partition(self, tmp_path):
        """Test loading a partition with no sources or deps"""
        empty_file = tmp_path / "empty.gbs.yaml"
        empty_file.write_text("{}")

        partition = load_partition(empty_file)
        assert partition.name == "empty"
        assert len(partition.groups) == 1
        root_cond = partition.groups[0].conditions[0]
        assert root_cond.is_default()
        assert len(root_cond.sources) == 0
        assert len(root_cond.deps) == 0


class TestLibraryLoader:
    """Tests for library loading"""

    def test_load_simple_library(self):
        """Test loading a simple library"""
        library_file = FIXTURES_DIR / "libraries" / "simple_lib.gbs.yaml"
        library = load_library(library_file)

        assert library.name == "simple_library"
        assert library.description == "A simple test library"
        assert len(library.partitions) == 1
        # Partition name comes from filename (simple.gbs.yaml -> simple)
        assert "simple" in library.partitions

    def test_load_library_no_partitions(self, tmp_path):
        """Test loading a library without partitions"""
        lib_file = tmp_path / "lib.gbs.yaml"
        lib_file.write_text("name: empty_lib\ndescription: Empty library\n")

        library = load_library(lib_file)
        assert library.name == "empty_lib"
        assert len(library.partitions) == 0

    def test_load_library_missing_partition_file(self, tmp_path):
        """Test loading library with missing partition file (should warn, not fail)"""
        lib_file = tmp_path / "lib.gbs.yaml"
        lib_file.write_text(
            "name: test_lib\n"
            "partitions:\n"
            "  - nonexistent_partition\n"
        )

        # Should load library but skip missing partition
        library = load_library(lib_file)
        assert library.name == "test_lib"
        assert len(library.partitions) == 0


class TestRepositoryLoader:
    """Tests for repository loading"""

    def test_load_simple_repository(self):
        """Test loading a simple repository"""
        repo_file = FIXTURES_DIR / "repositories" / "simple_repo.gbs.yaml"
        repository = load_repository(repo_file)

        assert repository.name == "simple_repository"
        assert repository.description == "A simple test repository"
        assert len(repository.libraries) == 1
        assert "simple_library" in repository.libraries

        # Check that library was loaded with its partition
        lib = repository.libraries["simple_library"]
        assert len(lib.partitions) == 1

    def test_load_repository_no_libraries(self, tmp_path):
        """Test loading a repository without libraries"""
        repo_file = tmp_path / "repo.gbs.yaml"
        repo_file.write_text("name: empty_repo\ndescription: Empty repository\n")

        repository = load_repository(repo_file)
        assert repository.name == "empty_repo"
        assert len(repository.libraries) == 0


class TestProjectLoader:
    """Tests for project loading"""

    def test_load_simple_project(self):
        """Test loading a simple project"""
        project_file = FIXTURES_DIR / "projects" / "simple.gbs.yaml"
        project = load_project(project_file)

        assert project.name == "simple_project"
        assert project.description == "A simple test project"
        assert project.topcell == "top"
        assert project.output_format == "bitstream"

        # Check toolsuite
        assert project.toolsuite.name == "vivado"
        assert project.toolsuite.backend == "gbs.backends.vivado"
        assert project.toolsuite.config["version"] == "2023.1"

        # Check filter vars
        assert project.filter_vars["vendor"] == "xilinx"
        assert project.filter_vars["family"] == "7series"

        # Check root library
        assert project.root_library.name == "project_root"
        assert len(project.root_library.partitions) == 1
        assert "top" in project.root_library.partitions

        # Check top partition
        top_partition = project.root_library.partitions["top"]
        assert len(top_partition.groups) == 1
        root_group = top_partition.groups[0]
        assert root_group.name == "root"

        # Check root condition
        root_cond = root_group.conditions[0]
        assert root_cond.is_default()
        assert "simple_library.simple" in root_cond.deps

        # Check sources
        assert len(root_cond.sources) == 2
        vhdl_sources = [s for s in root_cond.sources if s.language == Language.VHDL]
        other_sources = [s for s in root_cond.sources if s.language == Language.OTHER]
        assert len(vhdl_sources) == 1
        assert len(other_sources) == 1

    def test_load_project_missing_required_fields(self, tmp_path):
        """Test error when project is missing required fields"""
        project_file = tmp_path / "project.gbs.yaml"
        project_file.write_text("name: incomplete_project\n")

        with pytest.raises(LoadError, match="missing required field"):
            load_project(project_file)

    def test_load_project_invalid_toolsuite(self, tmp_path):
        """Test error when toolsuite is incomplete"""
        project_file = tmp_path / "project.gbs.yaml"
        project_file.write_text(
            "name: test\n"
            "toolsuite:\n"
            "  name: vivado\n"  # Missing backend
            "topcell: top\n"
            "output_format: bitstream\n"
            "root_library:\n"
            "  name: root\n"
        )

        with pytest.raises(LoadError, match="must specify 'name' and 'backend'"):
            load_project(project_file)


class TestSourceLoading:
    """Tests for source file loading"""

    def test_load_sources_with_variant(self, tmp_path):
        """Test loading sources with language variant"""
        partition_file = tmp_path / "partition.gbs.yaml"
        partition_file.write_text(
            "sources:\n"
            "  - language: vhdl\n"
            "    variant: '2008'\n"
            "    files:\n"
            "      - modern.vhd\n"
        )

        partition = load_partition(partition_file)
        root_cond = partition.groups[0].conditions[0]
        source = root_cond.sources[0]
        assert source.language == Language.VHDL
        assert source.variant == "2008"

    def test_load_sources_different_languages(self, tmp_path):
        """Test loading sources with different languages"""
        partition_file = tmp_path / "partition.gbs.yaml"
        partition_file.write_text(
            "sources:\n"
            "  - language: vhdl\n"
            "    files:\n"
            "      - code.vhd\n"
            "  - language: verilog\n"
            "    files:\n"
            "      - module.v\n"
            "  - language: systemverilog\n"
            "    files:\n"
            "      - module.sv\n"
        )

        partition = load_partition(partition_file)
        root_cond = partition.groups[0].conditions[0]
        sources = root_cond.sources
        assert len(sources) == 3
        assert sources[0].language == Language.VHDL
        assert sources[1].language == Language.VERILOG
        assert sources[2].language == Language.SYSTEMVERILOG

    def test_load_sources_invalid_language(self, tmp_path):
        """Test error with invalid language"""
        partition_file = tmp_path / "partition.gbs.yaml"
        partition_file.write_text(
            "sources:\n"
            "  - language: invalid_language\n"
            "    files:\n"
            "      - code.txt\n"
        )

        with pytest.raises(LoadError, match="Unknown language"):
            load_partition(partition_file)


class TestErrorHandling:
    """Tests for error handling"""

    def test_load_invalid_yaml(self, tmp_path):
        """Test error on invalid YAML syntax"""
        invalid_file = tmp_path / "invalid.yaml"
        invalid_file.write_text("name: test\ninvalid: [unclosed")

        with pytest.raises(LoadError, match="YAML parse error"):
            load_partition(invalid_file)

    def test_load_empty_yaml(self, tmp_path):
        """Test error on empty YAML file"""
        empty_file = tmp_path / "empty.yaml"
        empty_file.write_text("")

        with pytest.raises(LoadError, match="Empty or invalid YAML"):
            load_partition(empty_file)
