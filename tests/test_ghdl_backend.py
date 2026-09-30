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


class TestGhdlDeclaredInputs:
    """Everything GHDL reads is reachable from the declared task inputs"""

    @staticmethod
    def context(tmp_path, monkeypatch, backend):
        from types import SimpleNamespace
        from gbs.builtin.ghdl import dispatcher as ghdl_dispatcher
        from gbs.repository.model import SourceFile

        monkeypatch.setattr(ghdl_dispatcher.GHDLBaseDispatcher, "_get_ghdl_config",
                            lambda self: ("ghdl", backend))
        monkeypatch.setattr(ghdl_dispatcher.GHDLBaseDispatcher, "_get_ghdl_executable",
                            lambda self: "ghdl")
        monkeypatch.setattr(ghdl_dispatcher, "ghdl_version", lambda exe: "GHDL test")

        ctx = BuildContext(project=SimpleNamespace(name="p", root_library_name="work"),
                           base_output_path=tmp_path / "build")
        ctx.set_output_group_context(topcell="top", topcell_library="work",
                                     output_group=SimpleNamespace(name="sim"))

        sources = {}
        for library in ("dep", "work"):
            path = tmp_path / f"{library}.vhd"
            path.write_text("")
            sources[f"{library}.p"] = [SourceFile(path=path, file_type="vhdl")]
        ctx.populate_pending(SimpleNamespace(
            partitions=list(sources),
            sources=sources,
            partition_deps={"work.p": {"dep.p"}},
        ), {"vhdl"})
        return ctx

    @staticmethod
    async def dispatch(ctx):
        from gbs.builtin.ghdl.dispatcher import (
            GHDLAnalyzeDispatcher, GHDLSimulateDispatcher, GHDLRunDispatcher)

        dispatchers = [GHDLAnalyzeDispatcher(ctx), GHDLSimulateDispatcher(ctx),
                       GHDLRunDispatcher(ctx)]
        for _ in range(3):
            for d in dispatchers:
                await d.process()
        return dispatchers

    @pytest.mark.asyncio
    async def test_library_directory_is_an_import_output(self, tmp_path, monkeypatch):
        ctx = self.context(tmp_path, monkeypatch, "llvm")
        analyze, simulate, _ = await self.dispatch(ctx)

        for library in ("dep", "work"):
            cf, import_task = analyze.library_build_get(library, None)
            workdir, = import_task.outputs_of_type("ghdl-library-dir")
            assert workdir.directory
            assert workdir.library == library
            assert workdir.path == cf.path.parent
            assert workdir in list(simulate._linker.inputs)

    @pytest.mark.asyncio
    async def test_compiled_simulator_run_needs_no_library(self, tmp_path, monkeypatch):
        ctx = self.context(tmp_path, monkeypatch, "llvm")
        _, simulate, run = await self.dispatch(ctx)

        assert simulate._linker.outputs_of_type("ghdl-elab-dir") == []
        assert run._run_task.inputs_of_type("ghdl-library-dir") == []

    @pytest.mark.asyncio
    async def test_wrapper_simulator_run_reads_libraries(self, tmp_path, monkeypatch):
        """An mcode/jit simulator is a script running ghdl -r on the libraries"""
        ctx = self.context(tmp_path, monkeypatch, "mcode")
        _, simulate, run = await self.dispatch(ctx)

        elab_dir, = simulate._linker.outputs_of_type("ghdl-elab-dir")
        assert elab_dir.directory
        assert elab_dir.path.parent == ctx.output_path / "elab"

        run_inputs = list(run._run_task.inputs)
        assert elab_dir in run_inputs
        assert {r.library for r in run._run_task.inputs_of_type("ghdl-library-dir")} \
            == {"dep", "work"}

    @pytest.mark.asyncio
    async def test_vhpidirect_include_dirs_are_directory_inputs(self, tmp_path, monkeypatch):
        from gbs.builtin.ghdl import task as ghdl_task
        from gbs.builtin.ghdl.dispatcher import GHDLSimulateDispatcher
        from gbs.build.task import ResourceTypology

        ctx = self.context(tmp_path, monkeypatch, "llvm")
        include = tmp_path / "include"
        include.mkdir()
        c_path = tmp_path / "plugin.c"
        c_path.write_text("")
        c_source = ctx.get_resource(c_path, file_type="ghdl-vhpidirect-c",
                                    typology=ResourceTypology.SOURCE,
                                    metadata={"include_dirs": [include]})
        ctx.add_pending(c_source)

        GHDLSimulateDispatcher(ctx)._compile_vhpidirect_sources()

        compile_task, = (d for d in c_source.expected_by
                         if isinstance(d, ghdl_task.VHPIDirectCompile))
        include_dir, = compile_task.inputs_of_type("c-include-dir")
        assert include_dir.path == include.resolve()
        assert include_dir.directory

        argvs = []

        class FakeInvocation:
            returncode = 0

            def __init__(self, env=None, argv=None, cwd=None):
                argvs.append([str(a) for a in argv])

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        monkeypatch.setattr(ghdl_task, "GhdlInvocation", FakeInvocation)
        await compile_task.work()

        assert f"-I{include.resolve()}" in argvs[0]
