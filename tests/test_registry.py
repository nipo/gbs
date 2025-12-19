"""Tests for Backend discovery via PluginRegistry"""

import pytest
from pathlib import Path

from gbs.plugins import (
    BackendInfo,
    get_plugin_registry,
    reset_plugin_registry,
)
from gbs.protocol import Backend, Dispatcher
from gbs.base import BaseBackend, BasePass


# Mock passes for testing
class MockSimulatePass(BasePass):
    """Mock simulation pass"""
    name = "simulate"
    input_types = {"vhdl"}
    output_types = {"simulator"}


class MockSynthesizePass(BasePass):
    """Mock synthesis pass"""
    name = "synthesize"
    input_types = {"vhdl", "verilog"}
    output_types = {"netlist"}

    def contribute_filter_vars(self, config):
        return {"syn": 1}


class MockTransformPass(BasePass):
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
        from gbs.base import BaseDispatcher

        class TestDispatcher(BaseDispatcher):
            def __init__(self, context):
                super().__init__(context, "test_dispatcher_a", tool_name="test_a")

            async def process(self):
                pass

        from gbs.build import BuildContext
        ctx = BuildContext()
        return TestDispatcher(ctx)


class MockBackendB(BaseBackend):
    """Mock backend B with one pass"""

    def __init__(self):
        super().__init__("test.backend.b")

    def contribute_passes(self, config, output_types):
        if "vhdl" in output_types:
            return [MockTransformPass]
        return []

    def create_dispatcher(self, config):
        from gbs.base import BaseDispatcher

        class TestDispatcher(BaseDispatcher):
            def __init__(self, context):
                super().__init__(context, "test_dispatcher_b", tool_name="test_b")

            async def process(self):
                pass

        from gbs.build import BuildContext
        ctx = BuildContext()
        return TestDispatcher(ctx)


def test_registry_creation():
    """Test plugin registry provides backend access"""
    reset_plugin_registry()
    registry = get_plugin_registry()
    # At minimum, should discover built-in backends
    backends = registry.list_backends()
    assert isinstance(backends, list)


def test_global_registry_singleton():
    """Test that get_plugin_registry returns a singleton"""
    reset_plugin_registry()  # Clear any previous state

    registry1 = get_plugin_registry()
    registry2 = get_plugin_registry()

    assert registry1 is registry2


def test_reset_backend_registry():
    """Test resetting the global registry"""
    reset_plugin_registry()

    registry1 = get_plugin_registry()
    reset_plugin_registry()
    registry2 = get_plugin_registry()

    # Should be different instances after reset
    assert registry1 is not registry2


def test_backend_info_dataclass():
    """Test BackendInfo dataclass"""
    backend = MockBackendA()
    backend_info = BackendInfo(
        backend=backend,
        module_path="test.backend"
    )

    assert backend_info.backend is backend
    assert backend_info.module_path == "test.backend"


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
    reset_plugin_registry()
    registry = get_plugin_registry()

    # Should have loaded at least the 2 built-in backends (ghdl, gowin)
    # May also discover plugins if available
    backends = registry.list_backends()
    assert len(backends) >= 2
    assert "gbs.builtin.ghdl" in backends
    assert "gbs.builtin.gowin" in backends
