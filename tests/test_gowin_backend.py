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
    bitstream = context.get_resource(tmp_path / "impl" / "pnr" / "project.fs")
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
