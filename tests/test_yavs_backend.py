"""Tests for the yavs backend"""

from types import SimpleNamespace

import pytest

from gbs.build import BuildContext
from gbs.build.type_aliases import sibling_aliases
from gbs.builtin.yavs.backend import YavsBackend
from gbs.builtin.yavs.dispatcher import YavsDispatcher
from gbs.builtin.yavs.passes import YavsSimulatePass, YavsRunPass
from gbs.builtin.yavs.task import SimulatorInvocation, YavsInvocation
from gbs.repository.model import SourceFile
from gbs.ui.messages import MessageSeverity


class TestContribution:
    def test_simulator_contributes_simulate_pass(self):
        passes = YavsBackend().contribute_passes({}, {"simulator"})
        assert [type(p) for p in passes] == [YavsSimulatePass]

    @pytest.mark.parametrize("output", ["simulation-log", "simulation-success", "waveform-fst"])
    def test_run_outputs_contribute_run_pass(self, output):
        passes = YavsBackend().contribute_passes({}, {output})
        assert [type(p) for p in passes] == [YavsRunPass]

    def test_unrelated_output_contributes_nothing(self):
        assert YavsBackend().contribute_passes({}, {"bitstream"}) == []

    def test_filter_vars(self):
        p = YavsSimulatePass({"vhdl_standard": "2008"}, None, None)
        assert p.filter_vars() == {
            "purpose": "simulation",
            "vhdl_frontend": "yavs",
            "simulation_engine": "yavs",
            "vhdl_std": "2008",
        }

    def test_simulator_alias(self):
        assert "simulator" in sibling_aliases("yavs-simulator")


class TestDiagnostics:
    def test_located(self):
        m = YavsInvocation.diagnostic("/a/b.vhd:3:14: error[E1100]: `x` is not declared",
                                      MessageSeverity.INFO)
        assert (m.severity, m.identifier, m.file_path, m.line, m.column, m.message) == (
            MessageSeverity.ERROR, "E1100", "/a/b.vhd", 3, 14, "`x` is not declared")

    def test_unlocated(self):
        m = YavsInvocation.diagnostic("error[E0104]: top level `t` not found in library `w`",
                                      MessageSeverity.INFO)
        assert (m.severity, m.identifier, m.file_path) == (MessageSeverity.ERROR, "E0104", None)

    @pytest.mark.asyncio
    async def test_report_with_process(self):
        async def lines():
            for line in ["** Failure: 1000000fs+0: boom",
                         "    Process tb.process (/a/tb.vhd:7)",
                         "** Note: 0fs+0: hi",
                         "    Process tb.gen",
                         "plain"]:
                yield line

        proc = SimulatorInvocation(argv=["true"])
        msgs = [m async for m in proc.stdout_transform(lines())]
        assert len(msgs) == 3
        assert (msgs[0].severity, msgs[0].message, msgs[0].file_path, msgs[0].line) == (
            MessageSeverity.ERROR, "boom", "/a/tb.vhd", 7)
        assert (msgs[1].message, msgs[1].file_path, msgs[1].extended_message) == (
            "hi", None, "Process tb.gen")
        assert msgs[2].message == "plain"


def test_unsupported_standard_is_rejected(tmp_path):
    ctx = BuildContext(project=SimpleNamespace(name="p", root_library_name="work"),
                       base_output_path=tmp_path / "build")
    with pytest.raises(ValueError):
        YavsDispatcher(ctx, vhdl_std="2002")


@pytest.mark.asyncio
async def test_library_dependencies_are_mapped(tmp_path):
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

    dispatcher = YavsDispatcher(ctx)
    await dispatcher.process()
    await dispatcher.process()

    dep_task = dispatcher.library_task_get("dep")
    work_task = dispatcher.library_task_get("work")
    dep_lib, = dep_task.outputs_of_type("yavs-lib")
    work_lib, = work_task.outputs_of_type("yavs-lib")

    assert dep_lib.path.parent == dispatcher.library_workdir("dep").resolve()
    assert work_task.inputs_of_type("yavs-lib") == [dep_lib]
    assert [r.path.name for r in work_task.inputs_of_type("vhdl")] == ["work.vhd"]
    assert set(dispatcher.elaborate.inputs_of_type("yavs-lib")) == {dep_lib, work_lib}
