"""Tests for backend loader"""

import pytest
from pathlib import Path

# from gbs.backend.loader import  # REMOVED BackendLoader, BackendLoadError, load_backends_from_project
from gbs.backend.protocol import BaseBackend
from gbs.backend.registry import BackendRegistry
from gbs.build.context import BuildContext, BuildFileSet


class TestBackendLoader:
    """Tests for BackendLoader"""

    def test_loader_creation(self):
        """Test creating a backend loader"""
        loader = BackendLoader()
        assert loader is not None

    def test_load_backend_class_valid(self):
        """Test loading a valid backend class"""
        loader = BackendLoader()

        # Load GHDLDispatcher from gbs.backend module
        backend_class = loader.load_backend_class("gbs.backend:GHDLDispatcher")

        assert backend_class is not None
        assert backend_class.__name__ == "GHDLDispatcher"

    def test_load_backend_class_invalid_spec(self):
        """Test loading with invalid spec format"""
        loader = BackendLoader()

        with pytest.raises(BackendLoadError, match="Invalid backend spec"):
            loader.load_backend_class("invalid_spec")

    def test_load_backend_class_missing_module(self):
        """Test loading from non-existent module"""
        loader = BackendLoader()

        with pytest.raises(BackendLoadError, match="Failed to import module"):
            loader.load_backend_class("nonexistent.module:BackendClass")

    def test_load_backend_class_missing_class(self):
        """Test loading non-existent class from valid module"""
        loader = BackendLoader()

        with pytest.raises(BackendLoadError, match="has no class"):
            loader.load_backend_class("gbs.backend:NonExistentBackend")

    def test_load_backend_class_not_a_backend(self):
        """Test loading a class that doesn't implement Backend protocol"""
        loader = BackendLoader()

        # BuildContext is not a backend
        with pytest.raises(BackendLoadError, match="does not implement Backend protocol"):
            loader.load_backend_class("gbs.tasks:BuildContext")

    def test_create_backend_no_config(self):
        """Test creating a backend without configuration"""
        loader = BackendLoader()

        backend = loader.create_backend("gbs.backend:VerilogToVHDLBackend")

        assert backend is not None
        assert backend.name == "verilog_to_vhdl"
        assert backend.priority == 200

    def test_create_backend_with_config(self, tmp_path):
        """Test creating a backend with configuration"""
        loader = BackendLoader()

        backend = loader.create_backend(
            "gbs.backend:GHDLBackend",
            config={"output_dir": tmp_path / "build"}
        )

        assert backend is not None
        assert backend.name == "ghdl"
        assert backend.output_dir == tmp_path / "build"

    def test_create_backend_invalid_config(self):
        """Test creating backend with invalid configuration"""
        loader = BackendLoader()

        # GHDLBackend doesn't accept unknown kwargs
        with pytest.raises(BackendLoadError, match="Failed to instantiate"):
            loader.create_backend(
                "gbs.backend:GHDLBackend",
                config={"invalid_param": "value"}
            )

    def test_load_from_config_single_backend(self):
        """Test loading single backend from config"""
        loader = BackendLoader()

        config = [
            {
                "backend": "gbs.backend:VerilogToVHDLBackend",
                "config": {}
            }
        ]

        registry = loader.load_from_config(config)

        assert len(registry) == 1
        backends = list(registry)
        assert backends[0].name == "verilog_to_vhdl"

    def test_load_from_config_multiple_backends(self, tmp_path):
        """Test loading multiple backends from config"""
        loader = BackendLoader()

        config = [
            {
                "backend": "gbs.backend:MemInitBackend",
                "config": {}
            },
            {
                "backend": "gbs.backend:VerilogToVHDLBackend",
                "config": {}
            },
            {
                "backend": "gbs.backend:GHDLBackend",
                "config": {"output_dir": tmp_path / "build"}
            }
        ]

        registry = loader.load_from_config(config)

        assert len(registry) == 3

        # Check they're in priority order
        backends = list(registry)
        assert backends[0].name == "mem_init"  # priority 150
        assert backends[1].name == "verilog_to_vhdl"  # priority 200
        assert backends[2].name == "ghdl"  # priority 500

    def test_load_from_config_missing_backend_key(self):
        """Test loading with missing backend key"""
        loader = BackendLoader()

        config = [
            {
                "config": {}
            }
        ]

        with pytest.raises(BackendLoadError, match="missing 'backend' key"):
            loader.load_from_config(config)

    def test_load_from_config_empty_list(self):
        """Test loading from empty config list"""
        loader = BackendLoader()

        registry = loader.load_from_config([])

        assert len(registry) == 0

    def test_discover_entry_points(self):
        """Test discovering backends via entry points

        Note: This test may not find any entry points in the test environment.
        That's OK - we're testing the discovery mechanism works without errors.
        """
        loader = BackendLoader()

        discovered = loader.discover_entry_points()

        # Should return a dict (may be empty)
        assert isinstance(discovered, dict)

    def test_is_valid_backend(self):
        """Test backend validation"""
        from gbs.backend.protocol import Backend

        loader = BackendLoader()

        # Valid backends
        assert loader._is_valid_backend(GHDLBackend)
        assert loader._is_valid_backend(VerilogToVHDLBackend)

        # Invalid (not a backend)
        assert not loader._is_valid_backend(BuildContext)
        assert not loader._is_valid_backend(str)


class TestLoadBackendsFromProject:
    """Tests for load_backends_from_project convenience function"""

    def test_load_backends_from_project_with_backends(self, tmp_path):
        """Test loading backends from project config"""
        project = {
            "backends": [
                {
                    "backend": "gbs.backend:VerilogToVHDLBackend",
                    "config": {}
                },
                {
                    "backend": "gbs.backend:GHDLBackend",
                    "config": {"output_dir": tmp_path / "build"}
                }
            ]
        }

        registry = load_backends_from_project(project)

        assert len(registry) == 2
        backends = list(registry)
        assert backends[0].name == "verilog_to_vhdl"
        assert backends[1].name == "ghdl"

    def test_load_backends_from_project_no_backends(self):
        """Test loading from project without backends key"""
        project = {}

        registry = load_backends_from_project(project)

        assert len(registry) == 0

    def test_load_backends_from_project_empty_backends(self):
        """Test loading from project with empty backends list"""
        project = {"backends": []}

        registry = load_backends_from_project(project)

        assert len(registry) == 0


class TestBackendLoaderIntegration:
    """Integration tests for backend loader"""

    @pytest.mark.asyncio
    async def test_load_and_run_backends(self, tmp_path):
        """Test loading backends and running them"""
        from gbs.backend import run_backend_iteration
        from gbs.build import BuildResource

        # Create loader and load backends
        loader = BackendLoader()
        config = [
            {
                "backend": "gbs.backend:VerilogToVHDLBackend",
                "config": {}
            },
            {
                "backend": "gbs.backend:GHDLBackend",
                "config": {"output_dir": tmp_path / "build"}
            }
        ]
        registry = loader.load_from_config(config)

        # Create context and fileset
        ctx = BuildContext()
        ctx.project = type('obj', (object,), {
            'topcell': 'module',
            'root_library_name': 'work'
        })()
        fileset = BuildFileSet(ctx)

        # Add verilog file
        verilog_file = tmp_path / "module.v"
        fileset.add(BuildResource(
            resource=ctx.get_resource(verilog_file),
            file_type="verilog",
            library="work"
        ))

        # Run backends
        iterations = await run_backend_iteration(ctx, fileset, registry)

        # Should have transpiled and compiled
        assert iterations == 2
        assert len(fileset.filter(file_type="vhdl")) == 1
        assert len(fileset.filter(file_type="ghdl-simulator")) == 1

    def test_load_multiple_same_backend_different_config(self, tmp_path):
        """Test loading same backend class with different configurations

        This is useful for scenarios like multiple GHDL backends for different targets.
        """
        loader = BackendLoader()

        # This should fail because backends need unique names
        # But let's test that we can at least load them individually
        backend1 = loader.create_backend(
            "gbs.backend:GHDLBackend",
            config={"output_dir": tmp_path / "build1"}
        )
        backend2 = loader.create_backend(
            "gbs.backend:GHDLBackend",
            config={"output_dir": tmp_path / "build2"}
        )

        assert backend1.output_dir == tmp_path / "build1"
        assert backend2.output_dir == tmp_path / "build2"
        # Note: both will have same name "ghdl", so only one can be registered

    def test_load_backends_preserves_order(self):
        """Test that backends are loaded in config order before sorting by priority"""
        loader = BackendLoader()

        config = [
            {"backend": "gbs.backend:GHDLBackend", "config": {}},  # priority 500
            {"backend": "gbs.backend:MemInitBackend", "config": {}},  # priority 150
            {"backend": "gbs.backend:VerilogToVHDLBackend", "config": {}}  # priority 200
        ]

        registry = loader.load_from_config(config)

        # Should be sorted by priority
        backends = list(registry)
        assert backends[0].name == "mem_init"
        assert backends[1].name == "verilog_to_vhdl"
        assert backends[2].name == "ghdl"
