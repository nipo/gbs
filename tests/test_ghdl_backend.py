"""Tests for GHDL Backend with new architecture"""

import pytest
from pathlib import Path

from gbs.builtin.ghdl import get_backend
from gbs.builtin.ghdl.backend import GHDLBackend
from gbs.builtin.ghdl.passes import GHDLSimulatePass
from gbs.backend.protocol import Backend, BaseBackend
from gbs.backend.dispatcher import Dispatcher


def test_get_backend_returns_new_backend():
    """Test that get_backend returns new Backend instance"""
    backend = get_backend()

    assert isinstance(backend, GHDLBackend)
    assert isinstance(backend, BaseBackend)
    assert backend.name == "gbs.backend.ghdl"


def test_backend_implements_protocol():
    """Test that GHDLBackend implements Backend Protocol"""
    backend = get_backend()

    # Check that it has the required methods
    assert hasattr(backend, 'contribute_passes')
    assert hasattr(backend, 'create_dispatcher')
    assert callable(backend.contribute_passes)
    assert callable(backend.create_dispatcher)


def test_contribute_passes_with_simulator_output():
    """Test that backend contributes simulation pass when simulator is requested"""
    backend = get_backend()

    config = {"vhdl_standard": "2008"}
    output_types = {"ghdl-simulator"}

    passes = backend.contribute_passes(config, output_types)

    assert len(passes) == 1
    assert passes[0] == GHDLSimulatePass


def test_contribute_passes_with_generic_simulator():
    """Test that backend contributes pass for generic 'simulator' output"""
    backend = get_backend()

    config = {}
    output_types = {"simulator"}

    passes = backend.contribute_passes(config, output_types)

    assert len(passes) == 1
    assert passes[0] == GHDLSimulatePass


def test_contribute_passes_no_matching_output():
    """Test that backend returns empty list when no matching output"""
    backend = get_backend()

    config = {}
    output_types = {"netlist", "bitstream"}

    passes = backend.contribute_passes(config, output_types)

    assert passes == []


def test_create_dispatcher():
    """Test that backend creates dispatcher correctly"""
    backend = get_backend()

    config = {
        "vhdl_standard": "2008",
        "output_dir": "build/ghdl",
        "ghdl_tool": "ghdl"
    }

    dispatcher = backend.create_dispatcher(config)

    assert isinstance(dispatcher, Dispatcher)
    assert dispatcher.name == "ghdl"
    assert dispatcher.output_dir == Path("build/ghdl")
    assert dispatcher.vhdl_std == "2008"
    assert dispatcher.ghdl_tool == "ghdl"


def test_create_dispatcher_with_defaults():
    """Test that dispatcher creation uses default config"""
    backend = get_backend()

    config = {}

    dispatcher = backend.create_dispatcher(config)

    assert isinstance(dispatcher, Dispatcher)
    assert dispatcher.output_dir == Path("build")
    assert dispatcher.vhdl_std == "93c"
    assert dispatcher.ghdl_tool == "ghdl"


def test_ghdl_simulate_pass_metadata():
    """Test GHDLSimulatePass metadata"""
    assert GHDLSimulatePass.name == "ghdl-simulate"
    assert "vhdl" in GHDLSimulatePass.input_types
    assert "ghdl-simulator" in GHDLSimulatePass.output_types


def test_ghdl_simulate_pass_filter_vars():
    """Test GHDLSimulatePass filter variables"""
    pass_instance = GHDLSimulatePass()

    config = {"vhdl_standard": "2008"}
    filter_vars = pass_instance.contribute_filter_vars(config)

    assert filter_vars["target-usage"] == "simulation"
    assert filter_vars["compiler"] == "ghdl"
    assert filter_vars["vhdl-version"] == "2008"


def test_ghdl_simulate_pass_filter_vars_default():
    """Test GHDLSimulatePass filter variables with defaults"""
    pass_instance = GHDLSimulatePass()

    config = {}
    filter_vars = pass_instance.contribute_filter_vars(config)

    assert filter_vars["target-usage"] == "simulation"
    assert filter_vars["compiler"] == "ghdl"
    assert filter_vars["vhdl-version"] == "1993"  # Default
