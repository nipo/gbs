"""Tests for BackendRegistry"""

import pytest
from pathlib import Path

from gbs.backend.registry import (
    BackendRegistry,
    BackendInfo,
    get_backend_registry,
    reset_backend_registry,
)
from gbs.backend.protocol import BaseBackend, Backend
from gbs.planner.passes import Pass
from gbs.backend.dispatcher import Dispatcher


# Mock passes for testing
class MockSimulatePass(Pass):
    """Mock simulation pass"""
    name = "simulate"
    input_types = {"vhdl"}
    output_types = {"simulator"}


class MockSynthesizePass(Pass):
    """Mock synthesis pass"""
    name = "synthesize"
    input_types = {"vhdl", "verilog"}
    output_types = {"netlist"}

    def contribute_filter_vars(self, config):
        return {"syn": 1}


class MockTransformPass(Pass):
    """Mock transformation pass"""
    name = "transform"
    input_types = {"verilog"}
    output_types = {"vhdl"}


# Mock backends for testing
class MockBackendA(BaseBackend):
    """Mock backend A with two passes"""

    def __init__(self):
        super().__init__("test.backend.a")

    def contribute_passes(self, config, output_types):
        passes = []
        if "simulator" in output_types:
            passes.append(MockSimulatePass)
        if "netlist" in output_types:
            passes.append(MockSynthesizePass)
        return passes

    def create_dispatcher(self, config):
        from gbs.backend.dispatcher import BaseDispatcher

        class TestDispatcher(BaseDispatcher):
            def __init__(self):
                super().__init__("test_dispatcher_a")

            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        return TestDispatcher()


class MockBackendB(BaseBackend):
    """Mock backend B with one pass"""

    def __init__(self):
        super().__init__("test.backend.b")

    def contribute_passes(self, config, output_types):
        if "vhdl" in output_types:
            return [MockTransformPass]
        return []

    def create_dispatcher(self, config):
        from gbs.backend.dispatcher import BaseDispatcher

        class TestDispatcher(BaseDispatcher):
            def __init__(self):
                super().__init__("test_dispatcher_b")

            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        return TestDispatcher()


def test_registry_creation():
    """Test creating an empty registry"""
    registry = BackendRegistry()
    assert registry.list_backends() == []


def test_register_backend_manually():
    """Test manually registering a backend"""
    registry = BackendRegistry()
    backend = MockBackendA()
    registry._register_backend("test.backend.a", backend)

    # Check backend was registered
    backends = registry.list_backends()
    assert len(backends) == 1
    assert "test.backend.a" in backends


def test_get_backend():
    """Test getting a backend by module path"""
    registry = BackendRegistry()
    backend_a = MockBackendA()
    registry._register_backend("test.backend.a", backend_a)

    backend = registry.get_backend("test.backend.a")
    assert backend is backend_a

    backend = registry.get_backend("test.backend.nonexistent")
    assert backend is None


def test_get_backend_info():
    """Test getting backend info"""
    registry = BackendRegistry()
    backend_a = MockBackendA()
    registry._register_backend("test.backend.a", backend_a)

    backend_info = registry.get_backend_info("test.backend.a")
    assert backend_info is not None
    assert backend_info.backend is backend_a
    assert backend_info.module_path == "test.backend.a"


def test_register_multiple_backends():
    """Test registering multiple backends"""
    registry = BackendRegistry()
    backend_a = MockBackendA()
    backend_b = MockBackendB()
    registry._register_backend("test.backend.a", backend_a)
    registry._register_backend("test.backend.b", backend_b)

    backends = registry.list_backends()
    assert len(backends) == 2
    assert "test.backend.a" in backends
    assert "test.backend.b" in backends


def test_replace_existing_backend():
    """Test replacing an already registered backend"""
    registry = BackendRegistry()
    backend_a1 = MockBackendA()
    backend_a2 = MockBackendB()
    registry._register_backend("test.backend.a", backend_a1)
    registry._register_backend("test.backend.a", backend_a2)  # Replace

    backends = registry.list_backends()
    assert len(backends) == 1

    # Should have the second backend
    backend = registry.get_backend("test.backend.a")
    assert backend is backend_a2


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


def test_backend_info_dataclass():
    """Test BackendInfo dataclass"""
    backend = MockBackendA()
    backend_info = BackendInfo(
        backend=backend,
        module_path="test.backend"
    )

    assert backend_info.backend is backend
    assert backend_info.module_path == "test.backend"


def test_get_all_backends():
    """Test getting all backend instances"""
    registry = BackendRegistry()
    backend_a = MockBackendA()
    backend_b = MockBackendB()
    registry._register_backend("test.backend.a", backend_a)
    registry._register_backend("test.backend.b", backend_b)

    all_backends = registry.get_all_backends()
    assert len(all_backends) == 2
    assert backend_a in all_backends
    assert backend_b in all_backends


def test_backend_contribute_passes():
    """Test that backends can contribute passes"""
    backend = MockBackendA()

    # Request simulator
    passes = backend.contribute_passes({}, {"simulator"})
    assert len(passes) == 1
    assert passes[0] == MockSimulatePass

    # Request netlist
    passes = backend.contribute_passes({}, {"netlist"})
    assert len(passes) == 1
    assert passes[0] == MockSynthesizePass

    # Request both
    passes = backend.contribute_passes({}, {"simulator", "netlist"})
    assert len(passes) == 2


def test_backend_create_dispatcher():
    """Test that backends can create dispatchers"""
    backend = MockBackendA()

    dispatcher = backend.create_dispatcher({})
    assert isinstance(dispatcher, Dispatcher)


def test_discover_backends_loads_builtins():
    """Test that discover_backends loads built-in backends"""
    reset_backend_registry()
    registry = get_backend_registry()

    # Should have loaded the 4 built-in backends
    backends = registry.list_backends()
    assert len(backends) == 4
    assert "gbs.backend.ghdl" in backends
    assert "gbs.backend.gowin" in backends
    assert "gbs.backend.verilog_to_vhdl" in backends
    assert "gbs.backend.mem_init" in backends
