"""Tests for Pass and Backend base classes"""

from pathlib import Path
import pytest

from gbs.model.passes import Pass, Backend
from gbs.model.build import BuildContext, BuildResource, Resource


class MockPass(Pass):
    """Mock pass for testing"""
    name = "mock_pass"
    input_types = {"vhdl"}
    output_types = {"simulator"}

    async def execute(self, context, inputs):
        """Mock execute that just returns empty list"""
        return []


class AnotherMockPass(Pass):
    """Another mock pass for testing"""
    name = "another_pass"
    input_types = {"verilog"}
    output_types = {"netlist"}

    def contribute_filter_vars(self, config):
        return {"syn": 1}

    async def execute(self, context, inputs):
        return []


class MockBackend(Backend):
    """Mock backend for testing"""
    passes = [MockPass, AnotherMockPass]


def test_pass_attributes():
    """Test Pass class attributes"""
    pass_inst = MockPass()
    assert pass_inst.name == "mock_pass"
    assert "vhdl" in pass_inst.input_types
    assert "simulator" in pass_inst.output_types


def test_pass_contribute_filter_vars_default():
    """Test default contribute_filter_vars returns empty dict"""
    pass_inst = MockPass()
    result = pass_inst.contribute_filter_vars({})
    assert result == {}


def test_pass_contribute_filter_vars_custom():
    """Test custom contribute_filter_vars"""
    pass_inst = AnotherMockPass()
    result = pass_inst.contribute_filter_vars({})
    assert result == {"syn": 1}


def test_backend_get_passes():
    """Test Backend.get_passes()"""
    passes = MockBackend.get_passes()
    assert len(passes) == 2
    assert MockPass in passes
    assert AnotherMockPass in passes


def test_multiple_input_output_types():
    """Test pass with multiple input/output types"""
    class MultiPass(Pass):
        name = "multi"
        input_types = {"vhdl", "verilog", "systemverilog"}
        output_types = {"netlist", "timing"}

        async def execute(self, context, inputs):
            return []

    pass_inst = MultiPass()
    assert len(pass_inst.input_types) == 3
    assert len(pass_inst.output_types) == 2
    assert "vhdl" in pass_inst.input_types
    assert "netlist" in pass_inst.output_types
