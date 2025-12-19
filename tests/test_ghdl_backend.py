"""Tests for GHDL Backend with new architecture"""

import pytest
from pathlib import Path

from gbs.builtin.ghdl.backend import GHDLBackend
from gbs.builtin.ghdl.passes import GHDLSimulatePass
from gbs.protocol import Backend
from gbs.base import BaseBackend
from gbs.backend.dispatcher import Dispatcher
from gbs.build import BuildContext


def test_backend_creation():
    """Test that GHDLBackend can be instantiated"""
    backend = GHDLBackend()

    assert isinstance(backend, GHDLBackend)
    assert isinstance(backend, BaseBackend)
    assert backend.name == "gbs.builtin.ghdl"


def test_backend_implements_protocol():
    """Test that GHDLBackend implements Backend Protocol"""
    backend = GHDLBackend()

    # Check that it has the required methods
    assert hasattr(backend, 'contribute_passes')
    assert callable(backend.contribute_passes)


def test_contribute_passes_with_simulator_output():
    """Test that backend contributes simulation pass when simulator is requested"""
    backend = GHDLBackend()

    config = {"vhdl_standard": "2008"}
    output_types = {"ghdl-simulator"}

    passes = backend.contribute_passes(config, output_types)

    assert len(passes) == 1
    assert isinstance(passes[0], GHDLSimulatePass)


def test_contribute_passes_with_generic_simulator():
    """Test that backend contributes pass for generic 'simulator' output"""
    backend = GHDLBackend()

    config = {}
    output_types = {"simulator"}

    passes = backend.contribute_passes(config, output_types)

    assert len(passes) == 1
    assert isinstance(passes[0], GHDLSimulatePass)


def test_contribute_passes_no_matching_output():
    """Test that backend returns empty list when no matching output"""
    backend = GHDLBackend()

    config = {}
    output_types = {"netlist", "bitstream"}

    passes = backend.contribute_passes(config, output_types)

    assert passes == []


def test_pass_creates_dispatcher():
    """Test that pass creates dispatcher correctly"""
    config = {
        "vhdl_standard": "2008",
        "output_dir": "build/ghdl",
        "ghdl_tool": "ghdl"
    }

    pass_obj = GHDLSimulatePass(config)
    ctx = BuildContext()
    dispatchers = pass_obj.dispatchers(ctx)

    assert len(dispatchers) == 1
    dispatcher = dispatchers[0]
    assert isinstance(dispatcher, Dispatcher)
    assert dispatcher.name == "ghdl"
    # output_dir is no longer a dispatcher attribute - it's handled via BuildContext
    assert dispatcher.vhdl_std == "2008"
    assert dispatcher.ghdl_tool == "ghdl"


def test_pass_creates_dispatcher_with_defaults():
    """Test that pass creates dispatcher with default config"""
    config = {}

    pass_obj = GHDLSimulatePass(config)
    ctx = BuildContext()
    dispatchers = pass_obj.dispatchers(ctx)

    assert len(dispatchers) == 1
    dispatcher = dispatchers[0]
    assert isinstance(dispatcher, Dispatcher)
    # output_dir is no longer a dispatcher attribute - it's handled via BuildContext
    assert dispatcher.vhdl_std == "1993"
    assert dispatcher.ghdl_tool == "ghdl"


def test_ghdl_simulate_pass_metadata():
    """Test GHDLSimulatePass metadata"""
    assert GHDLSimulatePass.name == "ghdl-simulate"
    assert "vhdl" in GHDLSimulatePass.input_types
    assert "ghdl-simulator" in GHDLSimulatePass.output_types


def test_ghdl_simulate_pass_filter_vars():
    """Test GHDLSimulatePass filter variables"""
    config = {"vhdl_standard": "2008"}
    pass_instance = GHDLSimulatePass(config)

    filter_vars = pass_instance.filter_vars()

    assert filter_vars["target-usage"] == "simulation"
    assert filter_vars["compiler"] == "ghdl"
    assert filter_vars["vhdl-version"] == "2008"


def test_ghdl_simulate_pass_filter_vars_default():
    """Test GHDLSimulatePass filter variables with defaults"""
    config = {}
    pass_instance = GHDLSimulatePass(config)

    filter_vars = pass_instance.filter_vars()

    assert filter_vars["target-usage"] == "simulation"
    assert filter_vars["compiler"] == "ghdl"
    assert filter_vars["vhdl-version"] == "1993"  # Default
