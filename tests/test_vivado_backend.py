"""Tests for the Vivado synthesis and IP packaging backends

The tasks are driven with a session that records the TCL it is handed
instead of talking to Vivado, so the commands each flow emits are
checked verbatim.
"""

import pytest
import zipfile
from types import SimpleNamespace

from gbs.build import BuildContext
from gbs.build.task import BuildError
from gbs.builtin.vivado.dispatcher import VivadoDispatcher
from gbs.builtin.vivado.passes import VivadoSynthesizePass
from gbs.builtin.vivado.project import ProjectCommand
from gbs.builtin.vivado.task import NonProjectBuild
from gbs.builtin.vivado.vivado_tcl import Session
from gbs.builtin.vivado_ip import dispatcher as ip_dispatcher
from gbs.builtin.vivado_ip.dispatcher import VivadoIpDispatcher
from gbs.builtin.vivado_ip.passes import VivadoIpPackagePass
from gbs.builtin.vivado_ip.task import VivadoIpPackageTask
from gbs.ui.messages import MessageSeverity, ToolMessage


class RecordingSession(Session):
    """Session recording serialized commands, answering nothing"""

    def __init__(self):
        super().__init__(argv=["vivado"])
        self.commands = []

    async def interact(self, cmd):
        self.commands.append(self._cmd_serialize(cmd))
        return
        yield


class ErrorSession(RecordingSession):
    """Session answering with an ERROR to the commands matching a pattern"""

    def __init__(self, failing: str):
        super().__init__()
        self.failing = failing

    async def interact(self, cmd):
        text = self._cmd_serialize(cmd)
        self.commands.append(text)
        if self.failing in text:
            yield ToolMessage(severity=MessageSeverity.ERROR,
                              message="something went wrong",
                              identifier="Synth 8-1")


class MockDispatcher:
    def __init__(self, context):
        self.context = context
        self.name = "mock"
        self.tool_config = None


class OrderedContext:
    """Context serving its pending resources in a fixed library order"""

    def __init__(self, context, ordered):
        self.context = context
        self.ordered = ordered

    def __getattr__(self, name):
        return getattr(self.context, name)

    def get_pending_by_library_ordered(self):
        return self.ordered


class FakeGBSConfig:
    def __init__(self, path):
        self.path = path

    def get_tool(self, identifier):
        return SimpleNamespace(config={"path": str(self.path)})


def context_make(tmp_path, gbs_config=None):
    ctx = BuildContext(base_output_path=tmp_path, gbs_config=gbs_config)
    ctx.set_output_group_context(topcell="top", topcell_library="toplib",
                                 output_group=SimpleNamespace(name=""))
    return ctx


def sources_make(ctx, tmp_path):
    """One source of each type the add-source loops know about"""
    resources = []
    for name, file_type, version, library in [
            ("a.vhd", "vhdl", "2008", "liba"),
            ("b.vhd", "vhdl", "1993", "libb"),
            ("c.v", "verilog", None, "libc"),
            ("d.xdc", "xilinx-xdc", None, None),
            ("e.xci", "xilinx-xci", None, "libe"),
            ("f.tcl", "xilinx-constraints-tcl", None, None),
    ]:
        path = tmp_path / name
        path.write_text("")
        resources.append(ctx.get_resource(path, file_type=file_type,
                                          file_type_version=version,
                                          library=library))
    return resources


def vivado_install(tmp_path):
    """Create a directory layout resolve_tool_exe() accepts"""
    exe = tmp_path / "bin" / "vivado"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("")
    return exe


# --- Shared project helpers --------------------------------------------------

def project_command(tmp_path):
    ctx = context_make(tmp_path)
    session = RecordingSession()
    task = ProjectCommand(dispatcher=MockDispatcher(ctx), name="test",
                          session=session, inputs=[], outputs=[])
    return task, session


@pytest.mark.asyncio
async def test_ip_repos_setup(tmp_path):
    task, session = project_command(tmp_path)

    await task.ip_repos_setup(["/repo/a", "/repo/b"])

    assert session.commands == [
        "set_property {ip_repo_paths} [concat [get_property {ip_repo_paths} "
        "[current_project]] [list {/repo/a} {/repo/b}]] [current_project]",
        "update_ip_catalog {-rebuild}",
    ]


@pytest.mark.asyncio
async def test_ip_repos_setup_without_repository(tmp_path):
    task, session = project_command(tmp_path)

    await task.ip_repos_setup([])

    assert session.commands == []


@pytest.mark.asyncio
async def test_filesets_capture(tmp_path):
    task, session = project_command(tmp_path)

    await task.filesets_capture()

    assert session.commands == [
        "set source_fileset_obj [get_filesets {sources_1}]",
        "set constraints_fileset_obj [get_filesets {constrs_1}]",
    ]


@pytest.mark.asyncio
async def test_project_configure(tmp_path):
    task, session = project_command(tmp_path)

    await task.project_configure()

    assert session.commands == [
        "set_property {source_mgmt_mode} {DisplayOnly} [current_project]",
        "set_param {project.hsv.draftModeDefault} {only}",
        "set source_fileset_obj [get_filesets {sources_1}]",
        "set constraints_fileset_obj [get_filesets {constrs_1}]",
    ]


@pytest.mark.asyncio
async def test_source_mgmt_display_only(tmp_path):
    task, session = project_command(tmp_path)

    await task.source_mgmt_display_only()

    assert session.commands == [
        "set_property {source_mgmt_mode} {DisplayOnly} [current_project]",
    ]


@pytest.mark.asyncio
async def test_top_set(tmp_path):
    task, session = project_command(tmp_path)

    await task.top_set("blinky", "mylib")

    assert session.commands == [
        "set_property {top_lib} {mylib} $source_fileset_obj",
        "set_property {top} {blinky} $source_fileset_obj",
    ]


def test_vhdl_file_type():
    def resource(version):
        return SimpleNamespace(file_type_version=version)

    assert ProjectCommand.vhdl_file_type(resource("2008")) == "VHDL 2008"
    assert ProjectCommand.vhdl_file_type(resource("1993")) == "VHDL"
    assert ProjectCommand.vhdl_file_type(resource(None)) == "VHDL"


@pytest.mark.asyncio
async def test_bus_repo_fill(tmp_path):
    source = tmp_path / "bus.xml"
    source.write_text("<x/>")
    repo = tmp_path / "bus_repo"

    ProjectCommand.bus_repo_fill(repo, [SimpleNamespace(path=source)])

    assert (repo / "bus.xml").read_text() == "<x/>"


@pytest.mark.asyncio
async def test_ip_repo_paths_collect(tmp_path):
    ctx = context_make(tmp_path)
    ip_zip = tmp_path / "core.zip"
    with zipfile.ZipFile(ip_zip, "w") as zf:
        zf.writestr("component.xml", "<x/>")
    bus_zip = tmp_path / "buses.zip"
    with zipfile.ZipFile(bus_zip, "w") as zf:
        zf.writestr("bus.xml", "<x/>")
    bus_def = tmp_path / "other_bus.xml"
    bus_def.write_text("<x/>")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    inputs = [
        ctx.get_resource(ip_zip, file_type="vivado-ip-zip"),
        ctx.get_resource(repo_dir, file_type="vivado-ip-repository"),
        ctx.get_resource(bus_def, file_type="vivado-bus-definition"),
        ctx.get_resource(bus_zip, file_type="vivado-bus-zip"),
    ]
    task = ProjectCommand(dispatcher=MockDispatcher(ctx), name="test",
                          session=RecordingSession(), inputs=inputs, outputs=[])
    out = tmp_path / "out"

    paths = task.ip_repo_paths_collect(out)

    assert paths == [
        str(out / "ip_repo" / "core"),
        str(repo_dir),
        str(out / "bus_repo"),
    ]
    assert (out / "ip_repo" / "core" / "component.xml").exists()
    assert (out / "bus_repo" / "bus.xml").exists()
    assert (out / "bus_repo" / "other_bus.xml").exists()


# --- Per-backend source declaration ------------------------------------------

@pytest.mark.asyncio
async def test_synthesis_add_sources(tmp_path):
    ctx = context_make(tmp_path)
    session = RecordingSession()
    resources = sources_make(ctx, tmp_path)
    task = NonProjectBuild(dispatcher=MockDispatcher(ctx), session=session,
                           part="xc7a35tcsg324-1", inputs=resources, outputs=[])

    await task.sources_add(resources, 0.10, 0.05)

    assert session.commands == [
        "set {f} [add_files {-norecurse} {-fileset} $source_fileset_obj "
        f"[file {{normalize}} {{{tmp_path / 'a.vhd'}}}]]",
        "set_property {-dict} {file_type {VHDL 2008} library {liba}} $f",
        "set {f} [add_files {-norecurse} {-fileset} $source_fileset_obj "
        f"[file {{normalize}} {{{tmp_path / 'b.vhd'}}}]]",
        "set_property {-dict} {file_type {VHDL} library {libb}} $f",
        "set {f} [add_files {-norecurse} {-fileset} $source_fileset_obj "
        f"[file {{normalize}} {{{tmp_path / 'c.v'}}}]]",
        "set_property {-dict} {file_type {Verilog} library {libc}} $f",
        "set {f} [add_files {-norecurse} {-fileset} $constraints_fileset_obj "
        f"[file {{normalize}} {{{tmp_path / 'd.xdc'}}}]]",
        "set_property {-dict} {file_type {XDC} used_in {synthesis implementation}} $f",
        f"set {{f}} [read_ip {{{tmp_path / 'e.xci'}}}]",
        "set_property {-dict} {library {libe} used_in {synthesis implementation}} $f",
        "set {f} [add_files {-norecurse} {-fileset} $constraints_fileset_obj "
        f"[file {{normalize}} {{{tmp_path / 'f.tcl'}}}]]",
        "set_property {-dict} {file_type {TCL} used_in {synthesis implementation}} $f",
    ]


@pytest.mark.asyncio
async def test_ip_package_add_sources(tmp_path):
    ctx = context_make(tmp_path)
    session = RecordingSession()
    resources = sources_make(ctx, tmp_path)
    task = VivadoIpPackageTask(dispatcher=MockDispatcher(ctx), session=session,
                               part="xc7a35tcsg324-1", ip_config={},
                               inputs=resources, outputs=[])
    hdl_inputs = [r for r in resources if r.file_type in ("vhdl", "verilog")]
    xdc_inputs = task.inputs_of_type("xilinx-xdc")

    await task.sources_add(hdl_inputs + xdc_inputs, 0.1, 0.2)

    assert session.commands == [
        "set {f} [add_files {-norecurse} {-fileset} $source_fileset_obj "
        f"[file {{normalize}} {{{tmp_path / 'a.vhd'}}}]]",
        "set_property {-dict} {file_type {VHDL 2008} library {liba}} $f",
        "set last_source $f",
        "set {f} [add_files {-norecurse} {-fileset} $source_fileset_obj "
        f"[file {{normalize}} {{{tmp_path / 'b.vhd'}}}]]",
        "set_property {-dict} {file_type {VHDL} library {libb}} $f",
        "reorder_files {-after} [get_property {name} $last_source] "
        "[get_property {name} $f]",
        "set last_source $f",
        "set {f} [add_files {-norecurse} {-fileset} $source_fileset_obj "
        f"[file {{normalize}} {{{tmp_path / 'c.v'}}}]]",
        "set_property {-dict} {file_type {Verilog} library {libc}} $f",
        "reorder_files {-after} [get_property {name} $last_source] "
        "[get_property {name} $f]",
        "set last_source $f",
        "set {f} [add_files {-norecurse} {-fileset} $constraints_fileset_obj "
        f"[file {{normalize}} {{{tmp_path / 'd.xdc'}}}]]",
        "set_property {-dict} {file_type {XDC} used_in {synthesis implementation}} $f",
    ]


@pytest.mark.asyncio
async def test_sources_add_defaults_library_to_work(tmp_path):
    ctx = context_make(tmp_path)
    task, session = project_command(tmp_path)
    path = tmp_path / "nolib.vhd"
    path.write_text("")
    resource = ctx.get_resource(path, file_type="vhdl")

    await task.sources_add([resource], 0.0, 0.0)

    assert session.commands[1] == (
        "set_property {-dict} {file_type {VHDL} library {work}} $f")


# --- Dispatchers -------------------------------------------------------------

@pytest.mark.parametrize("factory", [VivadoDispatcher, VivadoIpDispatcher])
def test_dispatcher_session_argv(tmp_path, factory):
    exe = vivado_install(tmp_path)
    ctx = context_make(tmp_path, gbs_config=FakeGBSConfig(tmp_path))
    dispatcher = factory(context=ctx, target={"part": "xc7a35tcsg324-1"})

    session = dispatcher.session_get()

    assert session.argv == [str(exe), "-mode", "tcl", "-nojournal", "-nolog"]
    assert dispatcher.session_get() is session


@pytest.mark.parametrize("factory", [VivadoDispatcher, VivadoIpDispatcher])
def test_dispatcher_session_without_install(tmp_path, factory):
    ctx = context_make(tmp_path, gbs_config=FakeGBSConfig(tmp_path / "nowhere"))
    dispatcher = factory(context=ctx, target={"part": "xc7a35tcsg324-1"})

    with pytest.raises(RuntimeError, match="Vivado not found"):
        dispatcher.session_get()


def test_ip_dispatcher_input_order(tmp_path):
    ctx = context_make(tmp_path)
    by_name = {r.path.name: r for r in sources_make(ctx, tmp_path)}
    ordered = [
        ("libc", [by_name["c.v"]]),
        ("liba", [by_name["a.vhd"]]),
        (None, [by_name["d.xdc"], by_name["f.tcl"]]),
    ]
    dispatcher = VivadoIpDispatcher(context=OrderedContext(ctx, ordered),
                                    target={"part": "xc7a35tcsg324-1"})
    task = VivadoIpPackageTask(dispatcher=dispatcher, session=RecordingSession(),
                               part="xc7a35tcsg324-1", ip_config={},
                               inputs=[], outputs=[])

    dispatcher.inputs_attach(task, ip_dispatcher.ACCEPTED_INPUT_TYPES)

    assert [r.path.name for r in task.inputs] == ["c.v", "a.vhd", "d.xdc"]


# --- Passes ------------------------------------------------------------------

@pytest.mark.parametrize("factory", [VivadoSynthesizePass, VivadoIpPackagePass])
def test_pass_filter_vars(factory):
    config = {"target": {"part": "xc7a35tcsg324-1"}, "vhdl_standard": "2008"}

    filter_vars = factory(config).filter_vars()

    assert filter_vars["purpose"] == "synthesis"
    assert filter_vars["vendor"] == "xilinx"
    assert filter_vars["vhdl_frontend"] == "vivado"
    assert filter_vars["verilog_frontend"] == "vivado"
    assert filter_vars["synthesis_engine"] == "vivado"
    assert filter_vars["bitstream_engine"] == "vivado"
    assert filter_vars["vhdl_std"] == "2008"
    assert filter_vars["part"] == "xc7a35tcsg324-1"
    assert filter_vars["family"] == "artix7"


@pytest.mark.parametrize("factory", [VivadoSynthesizePass, VivadoIpPackagePass])
def test_pass_filter_vars_unparsable_part(factory, caplog):
    config = {"target": {"part": "xc7a35t"}}

    filter_vars = factory(config).filter_vars()

    assert filter_vars["part"] == "xc7a35t"
    assert "Cannot parse device" in caplog.text


def test_synthesis_pass_runs_pnr():
    config = {"target": {"part": "xc7a35tcsg324-1"}}

    assert VivadoSynthesizePass(config).filter_vars()["pnr_engine"] == "vivado"
    assert "pnr_engine" not in VivadoIpPackagePass(config).filter_vars()


# --- Error reporting ---------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesis_fails_on_error(tmp_path):
    ctx = context_make(tmp_path)
    session = ErrorSession("synth_design")
    task = NonProjectBuild(dispatcher=MockDispatcher(ctx), session=session,
                           part="xc7a35tcsg324-1", inputs=[], outputs=[])

    with pytest.raises(BuildError, match="synthesize the design"):
        await task.work()

    assert any("synth_design" in c for c in session.commands)
    assert not any("route_design" in c for c in session.commands)


@pytest.mark.asyncio
async def test_synthesis_fails_on_project_creation_error(tmp_path):
    ctx = context_make(tmp_path)
    session = ErrorSession("create_project")
    task = NonProjectBuild(dispatcher=MockDispatcher(ctx), session=session,
                           part="xc7a35tcsg324-1", inputs=[], outputs=[])

    with pytest.raises(BuildError, match="create the project"):
        await task.work()
