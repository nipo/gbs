from types import SimpleNamespace

import pytest

from gbs.build.context import BuildContext
from gbs.builtin.gowin.task import PnR, ProjectInit


class RecordingSession:
    def __init__(self):
        self.commands = []

    async def interact(self, command):
        self.commands.append(str(command))
        if False:
            yield None


class MockDispatcher:
    def __init__(self, context):
        self.context = context
        self.name = "gowin"
        self.device_info = SimpleNamespace(family="GW5AT-60", part="GW5AT-LV60PG484AC1/I0")


@pytest.mark.asyncio
async def test_pnr_restores_project_when_init_stamp_is_up_to_date(tmp_path):
    context = BuildContext(base_output_path=tmp_path)
    context.output_path = tmp_path
    context.get_target = lambda: {}
    context.get_topcell = lambda: "boundary"
    dispatcher = MockDispatcher(context)
    session = RecordingSession()

    source_path = tmp_path / "boundary.vhd"
    source_path.write_text("")
    source = context.get_resource(source_path, file_type="vhdl", library="work")
    stamp = context.get_stamp(".gowin_project_init.stamp")
    stamp.touch()

    project_init = ProjectInit(
        dispatcher=dispatcher,
        session=session,
        output_base_name="project",
        output_dir=tmp_path,
        inputs=[source],
        outputs=[stamp],
    )
    await project_init._work()
    assert session.commands == []

    netlist_path = tmp_path / "impl" / "gwsynthesis" / "project.vg"
    netlist_path.parent.mkdir(parents=True)
    netlist_path.write_text("")
    netlist = context.get_resource(netlist_path, file_type="gowin-netlist", library="work")
    bitstream = context.get_resource(tmp_path / "impl" / "pnr" / "project.fs", file_type="bitstream")
    pnr = PnR(
        dispatcher=dispatcher,
        session=session,
        project_init=project_init,
        inputs=[stamp, netlist],
        outputs=[bitstream],
    )
    await pnr.work()

    assert any(
        command.startswith("add_file")
        and "{-type} {netlist}" in command
        and str(netlist_path) in command
        for command in session.commands
    )
    assert not any(str(source_path) in command for command in session.commands)
    assert session.commands[-1] == "run {pnr}"
    assert project_init.initialized


@pytest.mark.asyncio
async def test_pnr_report_is_read_from_the_declared_report_directory(tmp_path):
    from gbs.builtin.gowin.task import AggregatePnrReport

    context = BuildContext(base_output_path=tmp_path)
    pnr_dir = tmp_path / "impl" / "pnr"
    pnr_dir.mkdir(parents=True)
    (pnr_dir / "design.rpt.html").write_text("<html><body>resource usage</body></html>")
    report_dir = context.get_resource(pnr_dir, file_type="gowin-pnr-report-dir",
                                      directory=True)
    dest = context.get_resource(tmp_path / "pnr-report.html", file_type="gowin-pnr-report")

    aggregate = AggregatePnrReport(
        dispatcher=MockDispatcher(context),
        output_base_name="design",
        inputs=[report_dir],
        outputs=[dest],
    )
    await aggregate.work()

    assert "resource usage" in dest.path.read_text()
