"""Tests for Pass planning metadata"""

from pathlib import Path
import pytest

from gbs.model.passes import Pass, PassMetadata


class MockPass(Pass):
    """Mock pass for testing"""
    name = "mock_pass"
    input_types = {"vhdl"}
    output_types = {"simulator"}


class AnotherMockPass(Pass):
    """Another mock pass for testing"""
    name = "another_pass"
    input_types = {"verilog"}
    output_types = {"netlist"}

    def contribute_filter_vars(self, config):
        return {"syn": 1}


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


def test_multiple_input_output_types():
    """Test pass with multiple input/output types"""

    class MultiPass(Pass):
        name = "multi"
        input_types = {"vhdl", "verilog", "systemverilog"}
        output_types = {"netlist", "timing"}

    pass_inst = MultiPass()
    assert len(pass_inst.input_types) == 3
    assert len(pass_inst.output_types) == 2
    assert "vhdl" in pass_inst.input_types
    assert "netlist" in pass_inst.output_types


def test_pass_with_priority():
    """Test pass with custom priority"""

    class HighPriorityPass(Pass):
        name = "high_priority"
        input_types = {"vhdl"}
        output_types = {"simulator"}
        priority = 10

    pass_inst = HighPriorityPass()
    assert pass_inst.priority == 10


def test_pass_with_fork():
    """Test pass with can_fork flag"""

    class ForkPass(Pass):
        name = "fork_pass"
        input_types = {"vhdl", "verilog"}
        output_types = {"netlist"}
        can_fork = True

    pass_inst = ForkPass()
    assert pass_inst.can_fork is True


def test_pass_str_representation():
    """Test Pass __str__ method"""
    pass_inst = MockPass()
    str_repr = str(pass_inst)
    assert "mock_pass" in str_repr
    assert "vhdl" in str_repr
    assert "simulator" in str_repr


def test_pass_repr():
    """Test Pass __repr__ method"""
    pass_inst = MockPass()
    repr_str = repr(pass_inst)
    assert "MockPass" in repr_str
    assert "mock_pass" in repr_str


def test_pass_metadata_creation():
    """Test PassMetadata creation"""
    metadata = PassMetadata(
        pass_class=MockPass,
        config={},
        backend_name="test_backend"
    )

    assert metadata.pass_class == MockPass
    assert metadata.backend_name == "test_backend"
    assert metadata.name == "mock_pass"
    assert metadata.input_types == {"vhdl"}
    assert metadata.output_types == {"simulator"}
    assert metadata.filter_vars == {}


def test_pass_metadata_with_filter_vars():
    """Test PassMetadata with filter variables"""
    metadata = PassMetadata(
        pass_class=AnotherMockPass,
        config={},
        backend_name="test_backend"
    )

    assert metadata.filter_vars == {"syn": 1}


def test_pass_metadata_with_config():
    """Test PassMetadata passes config to contribute_filter_vars"""

    class ConfigurablePass(Pass):
        name = "configurable"
        input_types = {"vhdl"}
        output_types = {"simulator"}

        def contribute_filter_vars(self, config):
            return {"mode": config.get("mode", "default")}

    metadata = PassMetadata(
        pass_class=ConfigurablePass,
        config={"mode": "simulation"},
        backend_name="test_backend"
    )

    assert metadata.filter_vars == {"mode": "simulation"}


def test_pass_no_execute_method():
    """Test that Pass no longer has execute() method"""
    pass_inst = MockPass()

    # Pass should NOT have execute() method
    assert not hasattr(pass_inst, 'execute') or not callable(getattr(pass_inst, 'execute', None))
