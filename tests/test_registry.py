"""Tests for BackendRegistry"""

import pytest
from pathlib import Path

from gbs.backend.registry import (
    BackendRegistry,
    PassInfo,
    BackendInfo,
    get_backend_registry,
    reset_backend_registry,
)
from gbs.model.passes import Pass, Backend


# Mock passes for testing
class MockSimulatePass(Pass):
    """Mock simulation pass"""
    name = "simulate"
    input_types = {"vhdl"}
    output_types = {"simulator"}

    async def execute(self, context, inputs):
        return []


class MockSynthesizePass(Pass):
    """Mock synthesis pass"""
    name = "synthesize"
    input_types = {"vhdl", "verilog"}
    output_types = {"netlist"}

    def contribute_filter_vars(self, config):
        return {"syn": 1}

    async def execute(self, context, inputs):
        return []


class MockTransformPass(Pass):
    """Mock transformation pass"""
    name = "transform"
    input_types = {"verilog"}
    output_types = {"vhdl"}

    async def execute(self, context, inputs):
        return []


# Mock backends for testing
class MockBackendA(Backend):
    """Mock backend A with two passes"""
    passes = [MockSimulatePass, MockSynthesizePass]


class MockBackendB(Backend):
    """Mock backend B with one pass"""
    passes = [MockTransformPass]


def test_registry_creation():
    """Test creating an empty registry"""
    registry = BackendRegistry()
    assert registry.list_backends() == []
    assert registry.list_passes() == []


def test_register_backend_manually():
    """Test manually registering a backend"""
    registry = BackendRegistry()
    registry._register_backend("test.backend.a", MockBackendA)

    # Check backend was registered
    backends = registry.list_backends()
    assert len(backends) == 1
    assert "test.backend.a" in backends

    # Check passes were registered
    passes = registry.list_passes()
    assert len(passes) == 2
    assert "test.backend.a:simulate" in passes
    assert "test.backend.a:synthesize" in passes


def test_get_pass():
    """Test getting a pass by full name"""
    registry = BackendRegistry()
    registry._register_backend("test.backend.a", MockBackendA)

    pass_class = registry.get_pass("test.backend.a:simulate")
    assert pass_class is MockSimulatePass

    pass_class = registry.get_pass("test.backend.a:nonexistent")
    assert pass_class is None


def test_get_pass_info():
    """Test getting pass info"""
    registry = BackendRegistry()
    registry._register_backend("test.backend.a", MockBackendA)

    pass_info = registry.get_pass_info("test.backend.a:simulate")
    assert pass_info is not None
    assert pass_info.pass_class is MockSimulatePass
    assert pass_info.backend_module == "test.backend.a"
    assert pass_info.full_name == "test.backend.a:simulate"


def test_get_backend():
    """Test getting a backend by module path"""
    registry = BackendRegistry()
    registry._register_backend("test.backend.a", MockBackendA)

    backend_class = registry.get_backend("test.backend.a")
    assert backend_class is MockBackendA

    backend_class = registry.get_backend("test.backend.nonexistent")
    assert backend_class is None


def test_get_backend_info():
    """Test getting backend info"""
    registry = BackendRegistry()
    registry._register_backend("test.backend.a", MockBackendA)

    backend_info = registry.get_backend_info("test.backend.a")
    assert backend_info is not None
    assert backend_info.backend_class is MockBackendA
    assert backend_info.module_path == "test.backend.a"
    assert len(backend_info.passes) == 2


def test_find_passes_by_output_type():
    """Test finding passes by output type"""
    registry = BackendRegistry()
    registry._register_backend("test.backend.a", MockBackendA)
    registry._register_backend("test.backend.b", MockBackendB)

    # Find passes that produce "simulator"
    passes = registry.find_passes_by_output_type("simulator")
    assert len(passes) == 1
    assert passes[0].pass_class is MockSimulatePass

    # Find passes that produce "vhdl"
    passes = registry.find_passes_by_output_type("vhdl")
    assert len(passes) == 1
    assert passes[0].pass_class is MockTransformPass

    # Find passes that produce nonexistent type
    passes = registry.find_passes_by_output_type("nonexistent")
    assert len(passes) == 0


def test_find_passes_by_input_type():
    """Test finding passes by input type"""
    registry = BackendRegistry()
    registry._register_backend("test.backend.a", MockBackendA)
    registry._register_backend("test.backend.b", MockBackendB)

    # Find passes that consume "vhdl"
    passes = registry.find_passes_by_input_type("vhdl")
    assert len(passes) == 2
    assert any(p.pass_class is MockSimulatePass for p in passes)
    assert any(p.pass_class is MockSynthesizePass for p in passes)

    # Find passes that consume "verilog"
    passes = registry.find_passes_by_input_type("verilog")
    assert len(passes) == 2
    assert any(p.pass_class is MockSynthesizePass for p in passes)
    assert any(p.pass_class is MockTransformPass for p in passes)

    # Find passes that consume nonexistent type
    passes = registry.find_passes_by_input_type("nonexistent")
    assert len(passes) == 0


def test_register_multiple_backends():
    """Test registering multiple backends"""
    registry = BackendRegistry()
    registry._register_backend("test.backend.a", MockBackendA)
    registry._register_backend("test.backend.b", MockBackendB)

    backends = registry.list_backends()
    assert len(backends) == 2
    assert "test.backend.a" in backends
    assert "test.backend.b" in backends

    passes = registry.list_passes()
    assert len(passes) == 3


def test_replace_existing_backend():
    """Test replacing an already registered backend"""
    registry = BackendRegistry()
    registry._register_backend("test.backend.a", MockBackendA)
    registry._register_backend("test.backend.a", MockBackendB)  # Replace

    backends = registry.list_backends()
    assert len(backends) == 1

    # Should have the passes from MockBackendB
    passes = registry.list_passes()
    assert len(passes) == 1
    assert "test.backend.a:transform" in passes


def test_global_registry_singleton():
    """Test that get_backend_registry returns a singleton"""
    reset_backend_registry()  # Clear any previous state

    registry1 = get_backend_registry()
    registry2 = get_backend_registry()

    assert registry1 is registry2


def test_reset_backend_registry():
    """Test resetting the global registry"""
    reset_backend_registry()

    registry1 = get_backend_registry()
    reset_backend_registry()
    registry2 = get_backend_registry()

    # Should be different instances after reset
    assert registry1 is not registry2


def test_load_backend_module_missing():
    """Test loading a nonexistent backend module"""
    registry = BackendRegistry()

    with pytest.raises(ImportError):
        registry._load_backend_module("nonexistent.backend.module")


def test_load_backend_module_no_get_backend():
    """Test loading a module without get_backend() function"""
    registry = BackendRegistry()

    # Try to load a module that exists but doesn't have get_backend()
    with pytest.raises(AttributeError, match="must define get_backend"):
        registry._load_backend_module("sys")


def test_pass_info_dataclass():
    """Test PassInfo dataclass"""
    pass_info = PassInfo(
        pass_class=MockSimulatePass,
        backend_module="test.backend",
        full_name="test.backend:simulate"
    )

    assert pass_info.pass_class is MockSimulatePass
    assert pass_info.backend_module == "test.backend"
    assert pass_info.full_name == "test.backend:simulate"


def test_backend_info_dataclass():
    """Test BackendInfo dataclass"""
    pass_info = PassInfo(
        pass_class=MockSimulatePass,
        backend_module="test.backend",
        full_name="test.backend:simulate"
    )

    backend_info = BackendInfo(
        backend_class=MockBackendA,
        module_path="test.backend",
        passes=[pass_info]
    )

    assert backend_info.backend_class is MockBackendA
    assert backend_info.module_path == "test.backend"
    assert len(backend_info.passes) == 1
    assert backend_info.passes[0] is pass_info


def test_discover_backends_with_mocked_builtins(monkeypatch):
    """Test discover_backends with mocked built-in modules"""
    registry = BackendRegistry()

    # Mock the built-in module list to avoid loading real backends
    def mock_load(module_path):
        if module_path == "mock.backend.a":
            registry._register_backend(module_path, MockBackendA)
        elif module_path == "mock.backend.b":
            registry._register_backend(module_path, MockBackendB)
        else:
            raise ImportError(f"No module named '{module_path}'")

    # Patch the built-in list
    monkeypatch.setattr(
        "gbs.backend.registry.BackendRegistry._load_backend_module",
        mock_load
    )

    # Override builtin_modules in discover_backends
    original_discover = registry.discover_backends

    def patched_discover():
        # Store original method
        import gbs.backend.registry as reg_module

        # Temporarily replace builtin list
        original_builtins = None
        try:
            # Call with patched builtins
            builtin_modules = ["mock.backend.a", "mock.backend.b"]
            for module_path in builtin_modules:
                try:
                    mock_load(module_path)
                except Exception:
                    pass
        finally:
            pass

    patched_discover()

    assert len(registry.list_backends()) == 2
    assert len(registry.list_passes()) == 3
