"""Tests for Quartus Backend, including quartus-qsys input support"""

import pytest
from types import SimpleNamespace

from gbs.builtin.quartus.backend import QuartusBackend
from gbs.builtin.quartus.passes import QuartusSynthesizePass
from gbs.builtin.quartus.dispatcher import QuartusDispatcher
from gbs.builtin.quartus.task import ProjectSetup, QuartusProjectExport, QuartusSubprocess
from gbs.ui.messages import MessageSeverity
from gbs.protocol import Dispatcher
from gbs.base import BaseBackend
from gbs.build import BuildContext
from gbs.build.task import ResourceTypology


# Minimal dispatcher stand-in, mirrors the MockDispatcher used in test_tasks.py
class MockDispatcher:
    def __init__(self, context):
        self.context = context
        self.name = "mock"


# Minimal gbs_config stand-in, just enough for BuildContext.get_tool()/
# QuartusDispatcher.get_tool_option() to resolve a tool "path"
class FakeGBSConfig:
    def get_tool(self, identifier):
        return SimpleNamespace(config={"path": "/opt/altera_pro/25.3.1"})


def test_backend_creation():
    """Test that QuartusBackend can be instantiated"""
    backend = QuartusBackend()

    assert isinstance(backend, QuartusBackend)
    assert isinstance(backend, BaseBackend)
    assert backend.name == "gbs.builtin.quartus"


def test_backend_implements_protocol():
    """Test that QuartusBackend implements Backend Protocol"""
    backend = QuartusBackend()

    assert hasattr(backend, 'contribute_passes')
    assert callable(backend.contribute_passes)


def test_contribute_passes_with_sof_output():
    """Test that backend contributes the synthesize pass when a part is selected"""
    backend = QuartusBackend()

    config = {"target": {"part": "10CL025YU256C8G"}}
    output_types = {"quartus-sof"}

    passes = backend.contribute_passes(config, output_types)

    assert len(passes) == 1
    assert isinstance(passes[0], QuartusSynthesizePass)


def test_contribute_passes_with_project_output():
    """Test that backend contributes the synthesize pass for quartus-project alone"""
    backend = QuartusBackend()

    config = {"target": {"part": "10CL025YU256C8G"}}
    output_types = {"quartus-project"}

    passes = backend.contribute_passes(config, output_types)

    assert len(passes) == 1
    assert isinstance(passes[0], QuartusSynthesizePass)


def test_contribute_passes_no_matching_output():
    """Test that backend returns empty list when no matching output requested"""
    backend = QuartusBackend()

    config = {"target": {"part": "10CL025YU256C8G"}}
    # Quartus produces bitstreams and reports, never simulators or
    # waveforms — those should get no pass contribution.
    output_types = {"simulator", "waveform-vcd"}

    passes = backend.contribute_passes(config, output_types)

    assert passes == []


def test_quartus_synthesize_pass_metadata():
    """Test QuartusSynthesizePass metadata, including the quartus-qsys input type"""
    assert QuartusSynthesizePass.name == "quartus-synthesize"
    assert "vhdl" in QuartusSynthesizePass.input_types
    assert "quartus-sdc" in QuartusSynthesizePass.input_types
    assert "quartus-pin-assignment" in QuartusSynthesizePass.input_types
    assert "quartus-qsys" in QuartusSynthesizePass.input_types
    assert "quartus-qsys-script" in QuartusSynthesizePass.input_types
    assert "quartus-sof" in QuartusSynthesizePass.output_types
    assert "quartus-jam" in QuartusSynthesizePass.output_types
    assert "quartus-rbf" in QuartusSynthesizePass.output_types
    assert "quartus-project" in QuartusSynthesizePass.output_types


def test_quartus_synthesize_pass_filter_vars():
    """Test QuartusSynthesizePass filter variables"""
    config = {"target": {"part": "10CL025YU256C8G"}, "vhdl_standard": "2008"}
    pass_instance = QuartusSynthesizePass(config)

    filter_vars = pass_instance.filter_vars()

    assert filter_vars["purpose"] == "synthesis"
    assert filter_vars["vendor"] == "altera"
    assert filter_vars["synthesis_engine"] == "quartus"
    assert filter_vars["vhdl_std"] == "2008"
    assert filter_vars["part"] == "10CL025YU256C8G"


def test_pass_creates_dispatcher():
    """Test that pass creates a QuartusDispatcher correctly"""
    config = {
        "target": {"part": "10CL025YU256C8G"},
        "vhdl_standard": "2008",
        "tool": "quartus",
    }

    pass_obj = QuartusSynthesizePass(config)
    ctx = BuildContext()
    dispatchers = pass_obj.dispatchers(ctx)

    assert len(dispatchers) == 1
    dispatcher = dispatchers[0]
    assert isinstance(dispatcher, Dispatcher)
    assert dispatcher.name == "quartus"
    assert dispatcher.device == "10CL025YU256C8G"
    assert dispatcher.vhdl_std == "2008"


@pytest.mark.asyncio
async def test_project_setup_emits_qip_file_assignment(tmp_path):
    """Test that a quartus-qip input produces a QIP_FILE assignment in the .qsf

    ProjectSetup.work() only writes text files and doesn't shell out to any
    tool, so this can be exercised directly without a Quartus install.
    """
    ctx = BuildContext(base_output_path=tmp_path)
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    # Mirrors the .qip path QuartusDispatcher._create_qsys_generate_task
    # computes: output_files/qsys/<system_name>/<system_name>.qip.
    qip_path = tmp_path / "output_files" / "qsys" / "sys" / "sys.qip"
    qip_resource = ctx.get_resource(qip_path, file_type="quartus-qip")

    setup_task = ProjectSetup(
        dispatcher=MockDispatcher(ctx),
        device="10CL025YU256C8G",
        vhdl_std="1993",
        project_name="project",
        inputs=[qip_resource],
        outputs=[],
    )

    await setup_task.work()

    qsf_text = (tmp_path / "project.qsf").read_text()
    assert f"set_global_assignment -name QIP_FILE {qip_path}" in qsf_text


@pytest.mark.asyncio
async def test_project_setup_emits_qip_file_before_sdc_file(tmp_path):
    """Test that QIP_FILE assignments always precede SDC_FILE, regardless of input order

    Quartus sources each IP core's own embedded SDC (which creates that
    core's internal clocks, e.g. a transceiver's recovered clock) while
    resolving its QIP_FILE assignment. A user SDC_FILE listed earlier in
    the .qsf gets evaluated before those clocks exist — confirmed against
    a real Agilex 5 design: Quartus warned (22198) "constraints reference
    clocks before they are created", and a set_clock_groups naming an
    IP-internal clock silently matched zero objects despite a correct
    pattern, purely because of assignment order in the .qsf.
    """
    ctx = BuildContext(base_output_path=tmp_path)
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    sdc_path = tmp_path / "timing.sdc"
    sdc_path.write_text("create_clock -name clock_i -period 20.000 [get_ports {PL8}]\n")
    sdc_resource = ctx.get_resource(sdc_path, file_type="quartus-sdc")

    qip_path = tmp_path / "output_files" / "qsys" / "sys" / "sys.qip"
    qip_resource = ctx.get_resource(qip_path, file_type="quartus-qip")

    # sdc added before qip, deliberately — the fix must not depend on caller order.
    setup_task = ProjectSetup(
        dispatcher=MockDispatcher(ctx),
        device="10CL025YU256C8G",
        vhdl_std="1993",
        project_name="project",
        inputs=[sdc_resource, qip_resource],
        outputs=[],
    )

    await setup_task.work()

    qsf_text = (tmp_path / "project.qsf").read_text()
    qip_line = qsf_text.index(f"set_global_assignment -name QIP_FILE {qip_path}")
    sdc_line = qsf_text.index(f"set_global_assignment -name SDC_FILE {sdc_path}")
    assert qip_line < sdc_line


@pytest.mark.asyncio
async def test_project_setup_emits_qip_file_for_nested_ip(tmp_path):
    """Test that nested .qip files under ip/<system_name>/ get their own QIP_FILE assignment

    qsys-generate breaks hardened/catalog IP cores (PLLs, HPS, EMIF,
    Generic Components, ...) out into their own .qip files under
    ip/<system_name>/<instance>/<instance>.qip, sibling to the top-level
    <system_name>/ output directory. Quartus doesn't pull these in
    automatically from the top-level .qip alone (confirmed against a
    real Agilex 5 design: instances stayed "undefined entity" errors
    until each nested .qip got its own QIP_FILE assignment), so
    ProjectSetup needs to discover and list them all.
    """
    ctx = BuildContext(base_output_path=tmp_path)
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    qsys_root = tmp_path / "output_files" / "qsys"
    qip_path = qsys_root / "sys" / "sys.qip"
    qip_resource = ctx.get_resource(qip_path, file_type="quartus-qip")

    nested_a = qsys_root / "ip" / "sys" / "sys_iopll_0" / "sys_iopll_0.qip"
    nested_b = qsys_root / "ip" / "sys" / "sys_hps_0" / "sys_hps_0.qip"
    for nested in (nested_a, nested_b):
        nested.parent.mkdir(parents=True)
        nested.touch()

    # Unrelated system's nested ip/ dir must not leak into sys's assignments.
    other_nested = qsys_root / "ip" / "other_sys" / "other_iopll_0" / "other_iopll_0.qip"
    other_nested.parent.mkdir(parents=True)
    other_nested.touch()

    setup_task = ProjectSetup(
        dispatcher=MockDispatcher(ctx),
        device="10CL025YU256C8G",
        vhdl_std="1993",
        project_name="project",
        inputs=[qip_resource],
        outputs=[],
    )

    await setup_task.work()

    qsf_text = (tmp_path / "project.qsf").read_text()
    assert f"set_global_assignment -name QIP_FILE {qip_path}" in qsf_text
    assert f"set_global_assignment -name QIP_FILE {nested_a}" in qsf_text
    assert f"set_global_assignment -name QIP_FILE {nested_b}" in qsf_text
    assert str(other_nested) not in qsf_text


@pytest.mark.asyncio
async def test_create_qsys_generate_task_places_qip_under_gbs_build(tmp_path):
    """Test that the .qip Resource is declared under gbs-build, not next to the source .qsys

    qsys-generate has no output-directory flag (verified against
    qsys-generate --help): it always writes <system_name>/<system_name>.qip
    as a sibling directory of whatever .qsys file it's given. GBS works
    around this by having QsysGenerate.work() stage a copy of the source
    .qsys under gbs-build before running the tool, so the resulting .qip
    stays scoped to this output group's build directory regardless of
    where the source .qsys lives (avoids races if the same .qsys were
    ever referenced by more than one output group).
    """
    ctx = BuildContext(base_output_path=tmp_path, gbs_config=FakeGBSConfig())
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    dispatcher = QuartusDispatcher(
        context=ctx,
        vhdl_std="1993",
        tool="quartus",
        target={"part": "10CL025YU256C8G"},
    )

    source_dir = tmp_path / "some" / "source" / "tree"
    qsys_resource = ctx.get_resource(source_dir / "my_system.qsys", file_type="quartus-qsys")

    qip_resource = dispatcher._create_qsys_generate_task(qsys_resource)

    expected = ctx.output_path / "output_files" / "qsys" / "my_system" / "my_system.qip"
    assert qip_resource.path == expected
    assert dispatcher.get_clean_paths() == {ctx.output_path}
    assert qip_resource.path.is_relative_to(ctx.output_path)


@pytest.mark.asyncio
async def test_create_qsys_generate_task_tracks_generic_component_ip_files(tmp_path):
    """Test that ip/<system_name>/*.ip files are attached as inputs to qsys_generate

    Generic Component (IP implementation type) instances store their
    configuration in a per-instance .ip file next to the .qsys rather
    than in the .qsys itself. Those files aren't declared as project
    sources, so nothing else would ever mark qsys_generate stale when one
    changes — this task must pick them up directly off disk.
    """
    ctx = BuildContext(base_output_path=tmp_path, gbs_config=FakeGBSConfig())
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    dispatcher = QuartusDispatcher(
        context=ctx,
        vhdl_std="1993",
        tool="quartus",
        target={"part": "10CL025YU256C8G"},
    )

    source_dir = tmp_path / "hdl"
    ip_dir = source_dir / "ip" / "my_system"
    ip_dir.mkdir(parents=True)
    (ip_dir / "my_system_some_instance.ip").write_text("<ipxact:component/>")
    (ip_dir / "my_system_other_instance.ip").write_text("<ipxact:component/>")

    qsys_resource = ctx.get_resource(source_dir / "my_system.qsys", file_type="quartus-qsys")

    qip_resource = dispatcher._create_qsys_generate_task(qsys_resource)

    qsys_task, = qip_resource.depends_on
    ip_inputs = {r.path for r in qsys_task.inputs if r.file_type == "quartus-qsys-ip"}
    assert ip_inputs == {
        ip_dir / "my_system_some_instance.ip",
        ip_dir / "my_system_other_instance.ip",
    }


@pytest.mark.asyncio
async def test_create_qsys_generate_task_without_ip_dir(tmp_path):
    """Test that a missing ip/<system_name>/ directory is a harmless no-op"""
    ctx = BuildContext(base_output_path=tmp_path, gbs_config=FakeGBSConfig())
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    dispatcher = QuartusDispatcher(
        context=ctx,
        vhdl_std="1993",
        tool="quartus",
        target={"part": "10CL025YU256C8G"},
    )

    qsys_resource = ctx.get_resource(tmp_path / "my_system.qsys", file_type="quartus-qsys")

    qip_resource = dispatcher._create_qsys_generate_task(qsys_resource)

    qsys_task, = qip_resource.depends_on
    assert [r for r in qsys_task.inputs if r.file_type == "quartus-qsys-ip"] == []


@pytest.mark.asyncio
async def test_create_qsys_script_task_wires_into_qsys_generate(tmp_path):
    """Test that a .tcl resource produces a quartus-qsys output resource

    Also verifies the qsys_script output (scripts/<name>/<name>.qsys) and
    the qsys_generate staging path (<name>.qsys, computed by
    _create_qsys_generate_task) don't collide, since both now live under
    the same output_files/qsys/ directory. Each script-generated system
    gets its own scripts/<name>/ subfolder (rather than a shared
    scripts/) since QsysScript.work() wipes that directory on every run.

    _create_qsys_script_task()/_create_qsys_generate_task() only build
    Resources and Task graph wiring, no subprocess is invoked, so this can
    be exercised directly without a Quartus install.
    """
    ctx = BuildContext(base_output_path=tmp_path, gbs_config=FakeGBSConfig())
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    dispatcher = QuartusDispatcher(
        context=ctx,
        vhdl_std="1993",
        tool="quartus",
        target={"part": "10CL025YU256C8G"},
    )

    tcl_resource = ctx.get_resource(tmp_path / "my_system.tcl", file_type="quartus-qsys-script")

    qsys_resource = dispatcher._create_qsys_script_task(tcl_resource)

    assert qsys_resource.file_type == "quartus-qsys"
    expected_qsys = ctx.output_path / "output_files" / "qsys" / "scripts" / "my_system" / "my_system.qsys"
    assert qsys_resource.path == expected_qsys

    qip_resource = dispatcher._create_qsys_generate_task(qsys_resource)

    assert qip_resource.file_type == "quartus-qip"
    expected_qip = ctx.output_path / "output_files" / "qsys" / "my_system" / "my_system.qip"
    assert qip_resource.path == expected_qip
    assert qip_resource.path.parent != qsys_resource.path.parent


@pytest.mark.asyncio
async def test_create_qsys_script_task_tracks_whole_ip_tree(tmp_path):
    """Test that _create_qsys_script_task tracks every .ip file under ip/, not just its own system's

    A qsys-script's add_component calls can reference any system's .ip
    files by relative path (confirmed against a real script exported
    from Platform Designer, which pulled components from a sibling
    system's ip/ subfolder) — so unlike _create_qsys_generate_task
    (which only needs ip/<system_name>/), this needs to track the whole
    tree for correct staleness detection.
    """
    ctx = BuildContext(base_output_path=tmp_path, gbs_config=FakeGBSConfig())
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    dispatcher = QuartusDispatcher(
        context=ctx,
        vhdl_std="1993",
        tool="quartus",
        target={"part": "10CL025YU256C8G"},
    )

    source_dir = tmp_path / "hdl"
    own_ip_dir = source_dir / "ip" / "my_system"
    other_ip_dir = source_dir / "ip" / "other_system"
    own_ip_dir.mkdir(parents=True)
    other_ip_dir.mkdir(parents=True)
    (own_ip_dir / "my_system_some_instance.ip").write_text("<ipxact:component/>")
    (other_ip_dir / "other_system_some_instance.ip").write_text("<ipxact:component/>")

    tcl_resource = ctx.get_resource(source_dir / "my_system.tcl", file_type="quartus-qsys-script")

    qsys_resource = dispatcher._create_qsys_script_task(tcl_resource)

    script_task, = qsys_resource.depends_on
    ip_inputs = {r.path for r in script_task.inputs if r.file_type == "quartus-qsys-ip"}
    assert ip_inputs == {
        own_ip_dir / "my_system_some_instance.ip",
        other_ip_dir / "other_system_some_instance.ip",
    }


@pytest.mark.asyncio
async def test_task_graph_create_project_only_skips_synthesis(tmp_path):
    """Test that requesting only quartus-project never builds the synthesis pipeline

    BuildContext._launch() launches every task ever created, regardless of
    whether anything depends on its output — so the only way to actually
    skip quartus_map/fit/sta/asm is to never construct them. This path
    returns before self.is_pro is read, so no real Quartus install is
    needed.
    """
    ctx = BuildContext(base_output_path=tmp_path, gbs_config=FakeGBSConfig())
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    dispatcher = QuartusDispatcher(
        context=ctx,
        vhdl_std="1993",
        tool="quartus",
        target={"part": "10CL025YU256C8G"},
    )

    project_dest = ctx.get_resource(
        tmp_path / "adc_bringup.qpf", file_type="quartus-project",
        typology=ResourceTypology.OUTPUT,
    )
    ctx.add_pending(project_dest)

    await dispatcher._task_graph_create()

    assert dispatcher._setup_task is not None
    assert dispatcher._map_task is None
    assert dispatcher._fit_task is None
    assert dispatcher._sta_task is None

    # QuartusProjectExport writes directly to the requested destination,
    # bypassing output_copy's own cleanup tracking, so gbs clean needs
    # QuartusDispatcher to report both exported files itself.
    assert dispatcher.get_clean_paths() == {
        ctx.output_path,
        tmp_path / "adc_bringup.qpf",
        tmp_path / "adc_bringup.qsf",
    }


@pytest.mark.asyncio
async def test_task_graph_create_project_and_sof_runs_both(tmp_path):
    """Test that requesting quartus-project alongside quartus-sof still runs the full pipeline

    Reaches self.is_pro (unlike the project-only path), but FakeGBSConfig's
    fake tool path makes it degrade gracefully to False (subprocess.run
    raises FileNotFoundError for the nonexistent quartus_sh, caught by
    is_pro's own except clause) — no real install needed here either.
    """
    ctx = BuildContext(base_output_path=tmp_path, gbs_config=FakeGBSConfig())
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    dispatcher = QuartusDispatcher(
        context=ctx,
        vhdl_std="1993",
        tool="quartus",
        target={"part": "10CL025YU256C8G"},
    )

    project_dest = ctx.get_resource(
        tmp_path / "quartus_project", file_type="quartus-project",
        typology=ResourceTypology.OUTPUT,
    )
    ctx.add_pending(project_dest)
    sof_dest = ctx.get_resource(
        tmp_path / "design.sof", file_type="quartus-sof",
        typology=ResourceTypology.OUTPUT,
    )
    ctx.add_pending(sof_dest)

    await dispatcher._task_graph_create()

    assert dispatcher._setup_task is not None
    assert dispatcher._map_task is not None
    assert dispatcher._fit_task is not None
    assert dispatcher._sta_task is not None


@pytest.mark.asyncio
async def test_task_graph_create_rbf_alone_runs_synthesis(tmp_path):
    """Test that requesting only quartus-rbf still runs the full synthesis pipeline

    quartus-rbf is produced from the .sof (via quartus_pfg), so it must be
    treated as synthesis-requiring in _task_graph_create's needs_synthesis
    check the same way quartus-jam already is — otherwise requesting rbf
    alone would incorrectly skip the very pipeline that produces its input.
    """
    ctx = BuildContext(base_output_path=tmp_path, gbs_config=FakeGBSConfig())
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    dispatcher = QuartusDispatcher(
        context=ctx,
        vhdl_std="1993",
        tool="quartus",
        target={"part": "10CL025YU256C8G"},
    )

    rbf_dest = ctx.get_resource(
        tmp_path / "design.rbf", file_type="quartus-rbf",
        typology=ResourceTypology.OUTPUT,
    )
    ctx.add_pending(rbf_dest)

    await dispatcher._task_graph_create()

    assert dispatcher._setup_task is not None
    assert dispatcher._map_task is not None
    assert dispatcher._fit_task is not None
    assert dispatcher._sta_task is not None


@pytest.mark.asyncio
async def test_quartus_project_export_regenerates_qpf_with_matching_name(tmp_path):
    """Test that QuartusProjectExport writes a .qpf/.qsf pair matching the requested filename

    A bare copy would leave the exported .qpf's PROJECT_REVISION pointing
    at the internal "project" name gbs-build/ always uses — Quartus uses
    that value to locate the matching .qsf, so under any other requested
    name a verbatim copy would silently break the pairing. Pure file I/O,
    no subprocess, so this can be exercised directly.
    """
    ctx = BuildContext(base_output_path=tmp_path)
    ctx.set_output_group_context(topcell="top", output_group=SimpleNamespace(name=""))

    qpf_resource = ctx.get_resource(tmp_path / "project.qpf", file_type="quartus-qpf")
    qsf_resource = ctx.get_resource(tmp_path / "project.qsf", file_type="quartus-qsf")
    qpf_resource.path.write_text('PROJECT_REVISION = "project"\n')
    qsf_resource.path.write_text("set_global_assignment -name DEVICE 10CL025YU256C8G\n")

    dest_dir = tmp_path / "exported"
    dest_dir.mkdir()
    qpf_dest = ctx.get_resource(dest_dir / "adc_bringup.qpf", file_type="quartus-project")

    export_task = QuartusProjectExport(
        dispatcher=MockDispatcher(ctx),
        inputs=[qpf_resource, qsf_resource],
        outputs=[qpf_dest],
    )

    await export_task.work()

    assert qpf_dest.path.is_file()
    assert qpf_dest.path.read_text() == 'PROJECT_REVISION = "adc_bringup"\n'

    qsf_dest = dest_dir / "adc_bringup.qsf"
    assert qsf_dest.is_file()
    assert qsf_dest.read_text() == qsf_resource.path.read_text()


def test_quartus_subprocess_classifies_timestamped_stderr_lines():
    """Test that qsys-generate/qsys-script's timestamped stderr lines are classified correctly

    Confirmed against the real tools: quartus_map/fit/sta/asm/pfg write
    "Info: ..."/"Warning: ..." to stdout with no timestamp, matching the
    original regex. qsys-generate/qsys-script instead write almost all
    their output to stderr, each line prefixed with their own timestamp
    (e.g. "2026.07.01.17:44:13 Warning: ...") — MessageSubprocess's base
    stderr_transform tags every stderr line ERROR unconditionally, so
    without QuartusSubprocess overriding it too, every qsys-generate/
    qsys-script message (Info included) showed up as an error.
    """
    proc = QuartusSubprocess(argv=["true"])

    warning = proc._classify(
        "2026.07.01.17:44:13 Warning: Quartus project not specified. Use --quartus-project and --rev to specify a Quartus project and revision."
    )
    assert warning.severity == MessageSeverity.WARNING

    info = proc._classify("2026.07.01.17:44:22 Info: Saving generation log to blink_generation.rpt")
    assert info.severity == MessageSeverity.INFO

    # Plain, non-timestamped format (quartus_map/fit/sta/asm/pfg) must
    # still work unchanged.
    plain_error = proc._classify("Error (19509): Cannot locate file adc_bringup.sof.")
    assert plain_error.severity == MessageSeverity.ERROR
    assert plain_error.identifier == "19509"
