"""Tests for GHDL Backend with new architecture"""

import pytest
from pathlib import Path

from gbs.builtin.ghdl.backend import GHDLBackend
from gbs.builtin.ghdl.passes import GHDLSimulatePass
from gbs.protocol import Backend
from gbs.base import BaseBackend
from gbs.protocol import Dispatcher
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
    config = {"vhdl_standard": "2008"}

    pass_obj = GHDLSimulatePass(config)
    ctx = BuildContext()
    dispatchers = pass_obj.dispatchers(ctx)

    assert len(dispatchers) == 1
    dispatcher = dispatchers[0]
    assert isinstance(dispatcher, Dispatcher)
    assert dispatcher.name == "ghdl-simulate"
    assert dispatcher.vhdl_std == "2008"
    assert dispatcher.tool_name == "ghdl"


def test_pass_creates_dispatcher_with_defaults():
    """Test that pass creates dispatcher with default config"""
    config = {}

    pass_obj = GHDLSimulatePass(config)
    ctx = BuildContext()
    dispatchers = pass_obj.dispatchers(ctx)

    assert len(dispatchers) == 1
    dispatcher = dispatchers[0]
    assert isinstance(dispatcher, Dispatcher)
    assert dispatcher.vhdl_std == "1993"
    assert dispatcher.tool_name == "ghdl"


def test_ghdl_simulate_pass_metadata():
    """Test GHDLSimulatePass metadata"""
    assert GHDLSimulatePass.name == "ghdl-simulate"
    assert "ghdl-cf" in GHDLSimulatePass.input_types
    assert "ghdl-simulator" in GHDLSimulatePass.output_types


def test_ghdl_simulate_pass_filter_vars():
    """Test GHDLSimulatePass filter variables"""
    config = {"vhdl_standard": "2008"}
    pass_instance = GHDLSimulatePass(config)

    filter_vars = pass_instance.filter_vars()

    assert filter_vars["purpose"] == "simulation"
    assert filter_vars["simulation_engine"].startswith("ghdl_")
    assert filter_vars["vhdl_frontend"].startswith("ghdl_")
    assert filter_vars["vhdl_std"] == "2008"


def test_ghdl_simulate_pass_filter_vars_default():
    """Test GHDLSimulatePass filter variables with defaults"""
    config = {}
    pass_instance = GHDLSimulatePass(config)

    filter_vars = pass_instance.filter_vars()

    assert filter_vars["purpose"] == "simulation"
    assert filter_vars["simulation_engine"].startswith("ghdl_")
    assert filter_vars["vhdl_frontend"].startswith("ghdl_")
    assert filter_vars["vhdl_std"] == "1993"


class TestGhdlSimulatorOutputPath:
    """Simulator output naming per platform and GHDL flavor"""

    @staticmethod
    def adjusted(monkeypatch, platform, flavor, name):
        from gbs.builtin.ghdl import passes
        monkeypatch.setattr(passes.sys, "platform", platform)
        monkeypatch.setattr(passes._GhdlFlavorProbe, "flavor",
                            classmethod(lambda cls, config, gbs_config: flavor))
        return GHDLSimulatePass({}).output_path("simulator", Path("out") / name)

    @pytest.mark.parametrize("flavor", ["mcode", "jit"])
    def test_batch_flavor_appends_cmd(self, monkeypatch, flavor):
        assert self.adjusted(monkeypatch, "win32", flavor, "sim") == Path("out/sim.cmd")

    @pytest.mark.parametrize("name", ["sim.bat", "sim.cmd", "sim.CMD"])
    def test_batch_suffix_kept(self, monkeypatch, name):
        assert self.adjusted(monkeypatch, "win32", "mcode", name) == Path("out") / name

    def test_batch_flavor_appends_to_other_suffix(self, monkeypatch):
        assert self.adjusted(monkeypatch, "win32", "mcode", "sim.exe") == Path("out/sim.exe.cmd")

    @pytest.mark.parametrize("flavor", ["gcc", "llvm"])
    def test_native_flavor_appends_exe(self, monkeypatch, flavor):
        assert self.adjusted(monkeypatch, "win32", flavor, "sim") == Path("out/sim.exe")

    def test_native_suffix_kept(self, monkeypatch):
        assert self.adjusted(monkeypatch, "win32", "llvm", "sim.EXE") == Path("out/sim.EXE")

    @pytest.mark.parametrize("flavor", ["mcode", "llvm"])
    def test_posix_unchanged(self, monkeypatch, flavor):
        assert self.adjusted(monkeypatch, "linux", flavor, "sim") == Path("out/sim")
