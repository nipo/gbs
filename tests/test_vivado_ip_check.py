"""Tests for the packaged Vivado IP synthesis check

The check task is driven with a session recording the TCL it is handed,
as in the backend tests, and a variant that fakes what synth_ip leaves
on disk.
"""

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from asyncclick.testing import CliRunner

from gbs.build import BuildContext
from gbs.build.task import BuildError, ConfigurationError, ResourceTypology
from gbs.builtin.vivado_ip.backend import VivadoIpBackend
from gbs.builtin.vivado_ip.check import IpCheck, IpCheckError
from gbs.builtin.vivado_ip.component import IpComponent
from gbs.builtin.vivado_ip.dispatcher import (
    VivadoIpCheckDispatcher,
    VivadoIpDispatcher,
)
from gbs.builtin.vivado_ip.task import VivadoIpCheckTask
from gbs.cli import cli
from gbs.planner.planner import BuildPlanner
from gbs.project.model import OutputFile, OutputGroup, ProjectModel
from gbs.project.partition import (
    ConditionalGroup,
    FilterCondition,
    PartitionTemplate,
)
from gbs.project.project import Project
from gbs.repository.model import SourceFile
from gbs.ui.messages import MessageSeverity, ToolMessage

from .test_vivado_backend import (
    ErrorSession,
    FakeGBSConfig,
    MockDispatcher,
    RecordingSession,
    vivado_install,
)


PART = "xc7z020clg400-1"
SPIRIT = "http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009"
IPXACT_2014 = "http://www.accellera.org/XMLSchema/IPXACT/1685-2014"
IPXACT_2022 = "http://www.accellera.org/XMLSchema/IPXACT/1685-2022"


def component_xml(namespace=SPIRIT, fields=None):
    fields = fields if fields is not None else dict(
        vendor="acme", library="ip", name="core", version="1.2")
    body = "".join(f"<p:{k}>{v}</p:{k}>" for k, v in fields.items())
    return (f'<?xml version="1.0"?><p:component xmlns:p="{namespace}">'
            f'{body}<p:model/></p:component>')


def ip_zip_make(path, members=None):
    members = members if members is not None else {
        "component.xml": component_xml()}
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def context_make(tmp_path, gbs_config=None):
    ctx = BuildContext(base_output_path=tmp_path, gbs_config=gbs_config)
    ctx.set_output_group_context(topcell="core", topcell_library="work",
                                 output_group=SimpleNamespace(name="og"))
    return ctx


def pending_make(ctx, path, file_type, typology):
    resource = ctx.get_resource(path, file_type=file_type, typology=typology)
    ctx.add_pending(resource)
    return resource


# --- IpComponent ---------------------------------------------------------------

class TestIpComponent:
    @pytest.mark.parametrize("namespace", [SPIRIT, IPXACT_2014, IPXACT_2022])
    def test_parse(self, namespace):
        component = IpComponent.parse(component_xml(namespace), "x")

        assert component == IpComponent("acme", "ip", "core", "1.2")
        assert component.vlnv == "acme:ip:core:1.2"

    def test_bad_namespace(self):
        with pytest.raises(BuildError, match="not an IP-XACT component"):
            IpComponent.parse(component_xml("http://example.com/other"), "x")

    def test_no_namespace(self):
        with pytest.raises(BuildError, match="not an IP-XACT component"):
            IpComponent.parse(b"<component/>", "x")

    def test_not_a_component(self):
        data = f'<p:busDefinition xmlns:p="{SPIRIT}"/>'
        with pytest.raises(BuildError, match="expected component"):
            IpComponent.parse(data, "x")

    def test_missing_field(self):
        data = component_xml(fields=dict(vendor="acme", library="ip",
                                         name="core"))
        with pytest.raises(BuildError, match="no version"):
            IpComponent.parse(data, "x")

    def test_empty_field(self):
        data = component_xml(fields=dict(vendor="acme", library=" ",
                                         name="core", version="1.0"))
        with pytest.raises(BuildError, match="no library"):
            IpComponent.parse(data, "x")

    def test_invalid_xml(self):
        with pytest.raises(BuildError, match="not a valid XML"):
            IpComponent.parse(b"<component", "x")

    def test_from_zip(self, tmp_path):
        path = ip_zip_make(tmp_path / "ip.zip", {
            "core/component.xml": component_xml(),
            "core/src/a.vhd": "",
        })

        assert IpComponent.from_zip(path).vlnv == "acme:ip:core:1.2"

    def test_from_zip_without_component(self, tmp_path):
        path = ip_zip_make(tmp_path / "ip.zip", {"src/a.vhd": ""})

        with pytest.raises(BuildError, match="no component.xml"):
            IpComponent.from_zip(path)

    def test_from_zip_with_two_components(self, tmp_path):
        path = ip_zip_make(tmp_path / "ip.zip", {
            "a/component.xml": component_xml(),
            "b/component.xml": component_xml(),
        })

        with pytest.raises(BuildError, match="several component.xml"):
            IpComponent.from_zip(path)

    def test_from_zip_not_a_zip(self, tmp_path):
        path = tmp_path / "ip.zip"
        path.write_text("nope")

        with pytest.raises(BuildError, match="cannot read IP zip"):
            IpComponent.from_zip(path)

    def test_from_dir(self, tmp_path):
        (tmp_path / "xgui").mkdir()
        (tmp_path / "component.xml").write_text(component_xml())

        assert IpComponent.from_dir(tmp_path).name == "core"

    def test_from_dir_without_component(self, tmp_path):
        with pytest.raises(BuildError, match="no component.xml"):
            IpComponent.from_dir(tmp_path)

    def test_from_dir_with_two_components(self, tmp_path):
        for sub in ("a", "b"):
            (tmp_path / sub).mkdir()
            (tmp_path / sub / "component.xml").write_text(component_xml())

        with pytest.raises(BuildError, match="several component.xml"):
            IpComponent.from_dir(tmp_path)

    async def test_load(self, tmp_path):
        ctx = context_make(tmp_path)
        path = ip_zip_make(tmp_path / "ip.zip")

        zip_resource = ctx.get_resource(path, file_type="vivado-ip-zip")
        assert IpComponent.load(zip_resource).name == "core"

        with pytest.raises(AssertionError):
            IpComponent.load(ctx.get_resource(path, file_type="vhdl"))


# --- Check task ----------------------------------------------------------------

class SynthSession(RecordingSession):
    """Session writing the checkpoint synth_ip would produce"""

    def __init__(self, ip_dir: Path, instance: str):
        super().__init__()
        self.ip_dir = ip_dir
        self.instance = instance

    async def interact(self, cmd):
        text = self._cmd_serialize(cmd)
        self.commands.append(text)
        if text.startswith("synth_ip"):
            dcp = self.ip_dir / self.instance / f"{self.instance}.dcp"
            dcp.parent.mkdir(parents=True)
            dcp.write_text("")
        return
        yield


class TestCheckTask:
    @staticmethod
    def task_make(tmp_path, session_factory, params=None):
        ctx = context_make(tmp_path)
        work_dir = (tmp_path / "og" / "ip-check").resolve()
        session = session_factory(work_dir / "ip", "core_0")
        ip = ctx.get_resource(ip_zip_make(tmp_path / "core.zip"),
                              file_type="vivado-ip-zip",
                              typology=ResourceTypology.SOURCE)
        report = ctx.get_resource(tmp_path / "reports" / "util.rpt",
                                  file_type="vivado-ip-synthesis-report",
                                  typology=ResourceTypology.OUTPUT)
        task = VivadoIpCheckTask(dispatcher=MockDispatcher(ctx),
                                 session=session, part=PART, ip=ip,
                                 params=params or {}, outputs=[report])
        return task, session, work_dir, report

    async def test_sequence(self, tmp_path):
        task, session, work_dir, report = self.task_make(
            tmp_path, SynthSession, {"WIDTH": 12, "FAST": True})
        stale = work_dir / "ip" / "stale.v"
        stale.parent.mkdir(parents=True)
        stale.write_text("")

        await task.work()

        ip_dir = work_dir / "ip"
        dcp = ip_dir / "core_0" / "core_0.dcp"
        repo = work_dir / "ip_repo" / "core"
        assert session.commands == [
            f"create_project {{-in_memory}} {{-part}} {{{PART}}}",
            "set_property {target_language} {VHDL} [current_project]",
            "set_property {source_mgmt_mode} {DisplayOnly} [current_project]",
            "set_param {project.hsv.draftModeDefault} {only}",
            "set source_fileset_obj [get_filesets {sources_1}]",
            "set constraints_fileset_obj [get_filesets {constrs_1}]",
            "set_property {ip_repo_paths} [concat [get_property "
            "{ip_repo_paths} [current_project]] "
            f"[list {{{repo}}}]] [current_project]",
            "update_ip_catalog {-rebuild}",
            "create_ip {-vlnv} {acme:ip:core:1.2} {-module_name} {core_0} "
            f"{{-dir}} {{{ip_dir}}}",
            "set_property {-dict} [list CONFIG.WIDTH {12} CONFIG.FAST {true}] "
            "[get_ips {core_0}]",
            "generate_target {all} [get_ips {core_0}]",
            "synth_ip [get_ips {core_0}]",
            f"open_checkpoint {{{dcp}}}",
            "foreach cell [get_cells {-quiet} {-hierarchical} {-filter} "
            "{IS_BLACKBOX}] {catch {send_msg_id {GBS 1-1} ERROR \"Black box "
            "cell $cell of unresolved module [get_property REF_NAME $cell]\"}}",
            f"report_utilization {{-file}} {{{report.path}}}",
            "close_design",
        ]
        assert not stale.exists()
        assert (repo / "component.xml").exists()
        assert report.path.parent.is_dir()

    async def test_without_params(self, tmp_path):
        task, session, _, _ = self.task_make(tmp_path, SynthSession)

        await task.work()

        assert not any("CONFIG." in c for c in session.commands)

    async def test_create_ip_failure(self, tmp_path):
        task, session, _, _ = self.task_make(
            tmp_path, lambda *_: ErrorSession("create_ip {-vlnv}"))

        with pytest.raises(BuildError, match="instantiate IP acme:ip:core:1.2"):
            await task.work()

        assert not any(c.startswith("synth_ip") for c in session.commands)

    async def test_synth_ip_failure(self, tmp_path):
        task, session, _, _ = self.task_make(
            tmp_path, lambda *_: ErrorSession("synth_ip ["))

        with pytest.raises(BuildError, match="synthesize the IP"):
            await task.work()

        assert not any("report_utilization" in c for c in session.commands)

    async def test_black_box_failure(self, tmp_path):
        class BlackBoxSession(SynthSession):
            async def interact(self, cmd):
                async for msg in super().interact(cmd):
                    yield msg
                if "IS_BLACKBOX" in self._cmd_serialize(cmd):
                    yield ToolMessage(severity=MessageSeverity.ERROR,
                                      message="Black box cell U0",
                                      identifier="GBS 1-1")

        task, session, _, _ = self.task_make(tmp_path, BlackBoxSession)

        with pytest.raises(BuildError, match="resolve every module"):
            await task.work()

        assert not any("report_utilization" in c for c in session.commands)

    async def test_missing_checkpoint(self, tmp_path):
        task, session, _, _ = self.task_make(
            tmp_path, lambda *_: RecordingSession())

        with pytest.raises(BuildError, match="without writing its checkpoint"):
            await task.work()

        assert not any("open_checkpoint" in c for c in session.commands)

    async def test_bad_params(self, tmp_path):
        task, session, _, _ = self.task_make(
            tmp_path, SynthSession, {"WIDTH": [1, 2]})

        with pytest.raises(BuildError, match="unsupported value"):
            await task.work()

        assert session.commands == []


class TestParamsTcl:
    def test_values(self):
        expansion = VivadoIpCheckTask.params_tcl(
            {"A": 1, "B": 2.5, "C": "x y", "D": False})

        assert str(expansion) == (
            "[list CONFIG.A {1} CONFIG.B {2.5} CONFIG.C {x y} CONFIG.D {false}]")

    @pytest.mark.parametrize("name", ["", "1A", "A.B", "A B", "A[0]"])
    def test_bad_name(self, name):
        with pytest.raises(BuildError, match="Invalid IP parameter name"):
            VivadoIpCheckTask.params_tcl({name: 1})

    @pytest.mark.parametrize("value", [None, [1], {"a": 1}])
    def test_bad_type(self, value):
        with pytest.raises(BuildError, match="unsupported value"):
            VivadoIpCheckTask.params_tcl({"A": value})

    @pytest.mark.parametrize("value", ["{", "a}b", "a\\"])
    def test_unquotable(self, value):
        with pytest.raises(BuildError, match="brace or a backslash"):
            VivadoIpCheckTask.params_tcl({"A": value})


# --- Dispatchers ---------------------------------------------------------------

class TestDispatchers:
    @staticmethod
    def context(tmp_path):
        vivado_install(tmp_path)
        return context_make(tmp_path, gbs_config=FakeGBSConfig(tmp_path))

    @staticmethod
    def packager(ctx):
        return VivadoIpDispatcher(context=ctx, target={"part": PART})

    @staticmethod
    def checker(ctx, params=None):
        return VivadoIpCheckDispatcher(context=ctx, target={"part": PART},
                                       params=params)

    @staticmethod
    def report(ctx, tmp_path):
        return pending_make(ctx, tmp_path / "util.rpt",
                            "vivado-ip-synthesis-report",
                            ResourceTypology.OUTPUT)

    async def test_source_ip(self, tmp_path):
        ctx = self.context(tmp_path)
        report = self.report(ctx, tmp_path)
        ip = pending_make(ctx, ip_zip_make(tmp_path / "core.zip"),
                          "vivado-ip-zip", ResourceTypology.SOURCE)
        checker = self.checker(ctx, {"W": 3})

        await checker.process()

        task = checker._check_task
        assert task.ip is ip
        assert task.params == {"W": 3}
        assert list(task.outputs) == [report]
        assert ctx.get_pending(ip.path) is None

    async def test_waits_for_report_goal(self, tmp_path):
        ctx = self.context(tmp_path)
        pending_make(ctx, ip_zip_make(tmp_path / "core.zip"),
                     "vivado-ip-zip", ResourceTypology.SOURCE)
        checker = self.checker(ctx)

        await checker.process()

        assert checker._check_task is None

    async def test_waits_for_produced_ip(self, tmp_path):
        ctx = self.context(tmp_path)
        self.report(ctx, tmp_path)
        goal = pending_make(ctx, tmp_path / "core.zip", "vivado-ip-zip",
                            ResourceTypology.OUTPUT)
        checker = self.checker(ctx)
        packager = self.packager(ctx)

        await checker.process()
        assert checker._check_task is None

        await packager.process()
        await checker.process()

        assert checker._check_task.ip is goal
        assert ctx.get_pending(goal.path) is goal

    async def test_prefers_zip_over_dir(self, tmp_path):
        ctx = self.context(tmp_path)
        self.report(ctx, tmp_path)
        pending_make(ctx, tmp_path / "core_dir", "vivado-ip-dir",
                     ResourceTypology.OUTPUT)
        goal = pending_make(ctx, tmp_path / "core.zip", "vivado-ip-zip",
                            ResourceTypology.OUTPUT)
        packager = self.packager(ctx)
        checker = self.checker(ctx)

        await packager.process()
        await checker.process()

        assert checker._check_task.ip is goal

    async def test_source_and_produced_ip(self, tmp_path):
        ctx = self.context(tmp_path)
        self.report(ctx, tmp_path)
        pending_make(ctx, ip_zip_make(tmp_path / "dep.zip"), "vivado-ip-zip",
                     ResourceTypology.SOURCE)
        pending_make(ctx, tmp_path / "core.zip", "vivado-ip-zip",
                     ResourceTypology.OUTPUT)
        packager = self.packager(ctx)
        checker = self.checker(ctx)

        await checker.process()
        assert checker._check_task is None

        await packager.process()
        with pytest.raises(ConfigurationError, match="vivado-ip-repository"):
            await checker.process()

    async def test_several_source_ips(self, tmp_path):
        ctx = self.context(tmp_path)
        self.report(ctx, tmp_path)
        for name in ("a.zip", "b.zip"):
            pending_make(ctx, ip_zip_make(tmp_path / name), "vivado-ip-zip",
                         ResourceTypology.SOURCE)
        checker = self.checker(ctx)

        with pytest.raises(ConfigurationError, match="single IP"):
            await checker.process()

    async def test_repositories_stay_pending(self, tmp_path):
        ctx = self.context(tmp_path)
        self.report(ctx, tmp_path)
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (tmp_path / "bus.xml").write_text("")
        (tmp_path / "top.vhd").write_text("")
        repositories = [
            pending_make(ctx, tmp_path / "bus.xml", "vivado-bus-definition",
                         ResourceTypology.SOURCE),
            pending_make(ctx, ip_zip_make(tmp_path / "bus.zip", {}),
                         "vivado-bus-zip", ResourceTypology.SOURCE),
            pending_make(ctx, repo_dir, "vivado-ip-repository",
                         ResourceTypology.SOURCE),
        ]
        hdl = pending_make(ctx, tmp_path / "top.vhd", "vhdl",
                           ResourceTypology.SOURCE)
        packager = self.packager(ctx)
        checker = self.checker(ctx)

        await packager.process()
        await checker.process()

        for resource in repositories:
            assert ctx.get_pending(resource.path) is resource
            assert resource in list(packager._package_task.inputs)
            assert resource in list(checker._check_task.inputs)
        assert ctx.get_pending(hdl.path) is None

    async def test_packager_leaves_source_ip(self, tmp_path):
        ctx = self.context(tmp_path)
        self.report(ctx, tmp_path)
        source = pending_make(ctx, ip_zip_make(tmp_path / "dep.zip"),
                              "vivado-ip-zip", ResourceTypology.SOURCE)
        packager = self.packager(ctx)

        await packager.process()

        outputs = list(packager._package_task.outputs)
        assert [r.path for r in outputs] == [(tmp_path / "og" / "ip.zip").resolve()]
        assert outputs[0].typology == ResourceTypology.INTERMEDIATE
        assert outputs[0].file_type == "vivado-ip-zip"
        assert source not in outputs
        assert source not in list(packager._package_task.inputs)

    async def test_packager_claims_outputs(self, tmp_path):
        ctx = self.context(tmp_path)
        goals = [
            pending_make(ctx, tmp_path / "core.zip", "vivado-ip-zip",
                         ResourceTypology.OUTPUT),
            pending_make(ctx, tmp_path / "core_dir", "vivado-ip-dir",
                         ResourceTypology.OUTPUT),
        ]
        packager = self.packager(ctx)

        await packager.process()

        assert list(packager._package_task.outputs) == goals


# --- Planner -------------------------------------------------------------------

class FakePlannerConfig:
    """GBS configuration where the vivado tool exists"""

    def __init__(self, path):
        self.path = path

    def get_tool(self, identifier):
        return SimpleNamespace(config={"path": str(self.path)})

    def apply_backend_overrides(self, backend_name, backend_config):
        return dict(backend_config)


class TestPlanner:
    @staticmethod
    def plan(tmp_path, source_types, output_types):
        template = PartitionTemplate(
            name="top",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default",
                    sources=[SourceFile(path=None, file_type=t)
                             for t in source_types])],
            )],
        )
        planner = BuildPlanner(
            [], [VivadoIpBackend()],
            gbs_config=FakePlannerConfig(tmp_path),
            root_partition_template=template,
        )
        og = OutputGroup(
            name="og", topcell="top", target={"part": PART},
            outputs=[OutputFile(type=t, path=Path(t))
                     for t in sorted(output_types)],
        )
        return sorted(p.name for p in planner.plan(og).passes)

    # These combine two passes producing unrelated outputs, and rely on
    # the planner requiring every requested output to be produced.
    @pytest.mark.parametrize("outputs", [
        {"vivado-ip-zip", "vivado-ip-synthesis-report"},
        {"vivado-ip-dir", "vivado-ip-synthesis-report"},
    ])
    def test_package_and_report(self, tmp_path, outputs):
        assert self.plan(tmp_path, {"vhdl", "xilinx-xdc"}, outputs) == [
            "vivado-ip-package", "vivado-ip-synthesize"]

    def test_report_only(self, tmp_path):
        assert self.plan(tmp_path, {"vhdl"},
                         {"vivado-ip-synthesis-report"}) == [
            "vivado-ip-package", "vivado-ip-synthesize"]

    def test_zip_source(self, tmp_path):
        sources = {"vivado-ip-zip", "vivado-ip-repository", "vivado-bus-zip"}
        assert self.plan(tmp_path, sources,
                         {"vivado-ip-synthesis-report"}) == [
            "vivado-ip-synthesize"]

    def test_package_only(self, tmp_path):
        assert self.plan(tmp_path, {"vhdl"}, {"vivado-ip-zip"}) == [
            "vivado-ip-package"]


# --- IpCheck -------------------------------------------------------------------

def project_make(groups):
    template = PartitionTemplate(name="top")
    model = ProjectModel(name="p", root_partition_templates={"top": template},
                         output_groups=groups)
    return Project(model=model, repositories=[], path=None, gbs_config=None)


def ip_group(name, config=None, output_type="vivado-ip-zip"):
    return OutputGroup(
        name=name, topcell="core", target={"part": PART},
        backend_config={IpCheck.BACKEND: config or {"vendor": "acme"}},
        outputs=[OutputFile(type=output_type, path=Path(f"/out/{name}.zip"))],
    )


class TestIpCheck:
    def test_zip_project(self, tmp_path):
        ip = ip_zip_make(tmp_path / "core.zip")
        repo = tmp_path / "repo"
        repo.mkdir()
        bus_zip = ip_zip_make(tmp_path / "bus.zip", {})

        project = IpCheck.zip_project(ip, PART, {"W": "3"}, [repo], [bus_zip],
                                      None, None)

        group, = project.model.output_groups
        assert group.name == "ip-check"
        assert group.topcell == "core"
        assert group.target == {"part": PART}
        assert group.require_backends == ["gbs.builtin.vivado-ip"]
        assert group.backend_config == {
            "gbs.builtin.vivado-ip": {"synthesis_check_config": {"W": "3"}}}
        assert group.outputs == [OutputFile(
            type="vivado-ip-synthesis-report",
            path=Path("gbs-build/ip-check/ip-utilization.rpt"))]

        template = project.model.get_root_partition_template(group)
        partition = template.evaluate({}, "work")
        assert [(s.path, s.file_type) for s in partition.sources] == [
            (ip.resolve(), "vivado-ip-zip"),
            (repo.resolve(), "vivado-ip-repository"),
            (bus_zip.resolve(), "vivado-bus-zip"),
        ]
        assert not template.has_deps()

    def test_zip_project_directory(self, tmp_path):
        (tmp_path / "component.xml").write_text(component_xml())

        project = IpCheck.zip_project(tmp_path, PART, {}, [], [],
                                      tmp_path / "r.rpt", None)

        group, = project.model.output_groups
        assert group.outputs[0].path == tmp_path / "r.rpt"
        template = project.model.get_root_partition_template(group)
        source, = template.evaluate({}, "work").sources
        assert source.file_type == "vivado-ip-dir"

    def test_zip_project_broken_zip(self, tmp_path):
        ip = ip_zip_make(tmp_path / "core.zip", {"src/a.vhd": ""})

        with pytest.raises(BuildError, match="no component.xml"):
            IpCheck.zip_project(ip, PART, {}, [], [], None, None)

    def test_project_extend_default_groups(self):
        sim = OutputGroup(name="sim", topcell="tb", outputs=[
            OutputFile(type="simulator", path=Path("/out/sim"))])
        a = ip_group("a", {"vendor": "acme",
                           "synthesis_check_config": {"W": 1, "D": 2}})
        b = ip_group("b", output_type="vivado-ip-dir")
        project = project_make([sim, a, b])
        a_config = a.backend_config[IpCheck.BACKEND]

        checked = IpCheck.project_extend(project, [], {"W": "5"}, None)

        assert checked == [
            ("a", Path("gbs-build/a/ip-utilization.rpt")),
            ("b", Path("gbs-build/b/ip-utilization.rpt")),
        ]
        groups = project.model.output_groups
        assert groups[0] is sim
        assert groups[1].backend_config[IpCheck.BACKEND] == {
            "vendor": "acme", "synthesis_check_config": {"W": "5", "D": 2}}
        assert groups[2].backend_config[IpCheck.BACKEND][
            "synthesis_check_config"] == {"W": "5"}
        assert groups[1].outputs[-1] == OutputFile(
            type="vivado-ip-synthesis-report",
            path=Path("gbs-build/a/ip-utilization.rpt"))
        assert a_config == {"vendor": "acme",
                            "synthesis_check_config": {"W": 1, "D": 2}}
        assert len(a.outputs) == 1

    def test_project_extend_named_group(self, tmp_path):
        project = project_make([ip_group("a"), ip_group("b")])

        checked = IpCheck.project_extend(project, ["b"], {},
                                         tmp_path / "r.rpt")

        assert checked == [("b", tmp_path / "r.rpt")]
        assert len(project.model.output_groups[0].outputs) == 1

    def test_project_extend_unknown_group(self):
        project = project_make([ip_group("a")])

        with pytest.raises(IpCheckError, match="Unknown output group"):
            IpCheck.project_extend(project, ["nope"], {}, None)

    def test_project_extend_group_without_ip(self):
        sim = OutputGroup(name="sim", topcell="tb", outputs=[
            OutputFile(type="simulator", path=Path("/out/sim"))])
        project = project_make([sim, ip_group("a")])

        with pytest.raises(IpCheckError, match="sim produce no packaged IP"):
            IpCheck.project_extend(project, ["sim"], {}, None)

    def test_project_extend_no_ip_at_all(self):
        sim = OutputGroup(name="sim", topcell="tb")
        project = project_make([sim])

        with pytest.raises(IpCheckError, match="No output group produces"):
            IpCheck.project_extend(project, [], {}, None)

    def test_project_extend_report_for_several_groups(self, tmp_path):
        project = project_make([ip_group("a"), ip_group("b")])

        with pytest.raises(IpCheckError, match="single output group"):
            IpCheck.project_extend(project, [], {}, tmp_path / "r.rpt")

    def test_params_parse(self):
        assert IpCheck.params_parse(("A=1", "B=x=y", "C=")) == {
            "A": "1", "B": "x=y", "C": ""}

    @pytest.mark.parametrize("spec, match", [
        ("A", "expected NAME=VALUE"),
        ("=1", "Invalid IP parameter name"),
        ("A.B=1", "Invalid IP parameter name"),
        ("A={", "brace"),
    ])
    def test_params_parse_errors(self, spec, match):
        with pytest.raises(IpCheckError, match=match):
            IpCheck.params_parse((spec,))


# --- CLI -----------------------------------------------------------------------

class TestCli:
    @staticmethod
    async def invoke(args):
        return await CliRunner().invoke(cli, ["vivado", "ip-check", *args])

    @pytest.mark.parametrize("args, message", [
        ([], "either an IP or --project"),
        (["--project", "IP"], "either an IP or --project"),
        (["IP"], "needs --part"),
        (["--project", "--part", PART], "--part only applies to an IP"),
        (["IP", "--part", PART, "-g", "a"], "only applies with --project"),
        (["IP", "--part", PART, "-c", "nope"], "expected NAME=VALUE"),
    ])
    async def test_usage_errors(self, tmp_path, monkeypatch, args, message):
        monkeypatch.chdir(tmp_path)
        ip_zip_make(tmp_path / "IP")

        result = await self.invoke(args)

        assert result.exit_code == 2
        assert message in result.output

    async def test_repo_must_be_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ip_zip_make(tmp_path / "ip.zip")

        result = await self.invoke(["ip.zip", "--part", PART,
                                    "--repo", "ip.zip"])

        assert result.exit_code == 2
        assert "is a file" in result.output

    async def test_broken_zip_fails_before_vivado(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ip_zip_make(tmp_path / "ip.zip", {"src/a.vhd": ""})

        def no_planning(*args, **kwargs):
            raise AssertionError("planned a broken IP")

        monkeypatch.setattr(Project, "realizations", no_planning)

        result = await self.invoke(["ip.zip", "--part", PART])

        assert result.exit_code == 1
        assert "no component.xml" in result.output
