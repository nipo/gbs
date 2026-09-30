"""Planning through a remote gbs served by `gbs remote serve --stdio` in a
child process, with its own home, configuration and working directory"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

import gbs
from gbs.config.model import GBSConfig, ToolConfig
from gbs.planner.planner import BuildPlanner, PlanningError
from gbs.plugins import get_plugin_registry
from gbs.project import Project
from gbs.project.model import OutputFile, OutputGroup
from gbs.project.partition import ConditionalGroup, FilterCondition, PartitionTemplate
from gbs.remote import (
    LocalToolHost, PassContribution, PassDescriptor,
    RemoteHost, RemotePass, WireError,
)
from gbs.repository.model import SourceFile

SRC = Path(gbs.__file__).resolve().parent.parent
YOSYS_ICE40 = "gbs.builtin.yosys.passes:YosysIce40Pass"


class RemoteSide:
    """Home, cache and working directory of a served gbs"""

    def __init__(self, root: Path, tools: str):
        self.home = root / "home"
        (self.home / ".config").mkdir(parents=True)
        self.yosys = self.home / "bin" / "yosys"
        self.yosys.parent.mkdir()
        self.yosys.write_text("")
        (self.home / ".config" / "gbs.yaml").write_text(tools.format(yosys=self.yosys))
        self.work = root / "work"
        self.work.mkdir()
        self.cache = root / "cache"
        self.env = dict(os.environ)
        self.env.update({
            "HOME": str(self.home),
            "XDG_CACHE_HOME": str(self.cache),
            # A different HOME hides user site-packages: pass the whole path
            "PYTHONPATH": os.pathsep.join([str(SRC)] + [p for p in sys.path if p]),
        })

    async def connect(self) -> RemoteHost:
        return await RemoteHost.connect(
            "test", [sys.executable, "-m", "gbs", "remote", "serve", "--stdio"],
            env=self.env, cwd=str(self.work))


YOSYS_USABLE = "tools:\n  - name: yosys\n    config: {{executable: {yosys}}}\n"
YOSYS_MISSING = "tools:\n  - name: yosys\n    config: {{executable: {yosys}.missing}}\n"


class Planning:
    """An ice40 synthesis only yosys can plan"""

    TEMPLATE = PartitionTemplate(
        name="top",
        groups=[ConditionalGroup(
            name="root",
            conditions=[FilterCondition(
                expression="default",
                sources=[SourceFile(path=None, file_type="verilog")])],
        )],
    )

    @staticmethod
    def output_group() -> OutputGroup:
        return OutputGroup(
            name="synth", topcell="top", target={"part": "iCE40UP5K-SG48I"},
            outputs=[OutputFile(type="ice40-netlist-json", path=Path("top.json"))],
        )

    @staticmethod
    def local_config(tmp_path: Path, yosys: bool) -> GBSConfig:
        if not yosys:
            return GBSConfig()
        exe = tmp_path / "local-yosys"
        exe.write_text("")
        return GBSConfig(tools=[ToolConfig("yosys", None, None, {"executable": str(exe)})])

    @classmethod
    def planner(cls, gbs_config: GBSConfig, hosts: list) -> BuildPlanner:
        return BuildPlanner(
            [], get_plugin_registry().get_all_backends(), {}, gbs_config,
            root_partition_template=cls.TEMPLATE,
            tool_hosts=hosts + [LocalToolHost(gbs_config)],
        )

    @classmethod
    async def plan(cls, gbs_config: GBSConfig, hosts: list):
        return await cls.planner(gbs_config, hosts).plan(cls.output_group())


async def test_tool_only_on_remote(tmp_path):
    remote = RemoteSide(tmp_path / "remote", YOSYS_USABLE)
    config = Planning.local_config(tmp_path, yosys=False)
    with pytest.raises(PlanningError, match="not configured on local host"):
        await Planning.plan(config, [])

    async with await remote.connect() as host:
        plan = await Planning.plan(config, [host.tool_host])

    pm, = plan.passes
    assert isinstance(pm.pass_obj, RemotePass)
    assert pm.pass_obj.host == "test"
    assert pm.name == "yosys-ice40"
    assert pm.backend_name == "gbs.builtin.yosys"
    assert pm.pass_class == YOSYS_ICE40
    assert "ice40-netlist-json" in pm.output_types
    assert plan.filter_vars["synthesis_engine"] == "yosys"
    assert plan.types_with_library == {"vhdl", "verilog"}
    assert plan.output_path(Planning.output_group().outputs[0]) == Path("top.json")

    descriptor = PassDescriptor.from_metadata(pm)
    assert descriptor.pass_class == YOSYS_ICE40
    assert descriptor.config["target"] == {"part": "iCE40UP5K-SG48I"}
    assert "ice40-netlist-json" in descriptor.requested_types

    with pytest.raises(AssertionError, match="its segment dispatches it"):
        pm.pass_obj.dispatchers(None)
    assert list(remote.work.iterdir()) == []


async def test_remote_wins_over_local(tmp_path):
    remote = RemoteSide(tmp_path / "remote", YOSYS_USABLE)
    config = Planning.local_config(tmp_path, yosys=True)
    local_plan = await Planning.plan(config, [])
    assert not isinstance(local_plan.passes[0].pass_obj, RemotePass)

    async with await remote.connect() as host:
        plan = await Planning.plan(config, [host.tool_host])

    pm, = plan.passes
    assert isinstance(pm.pass_obj, RemotePass)
    assert pm == local_plan.passes[0]


async def test_local_kept_when_remote_rejects(tmp_path):
    remote = RemoteSide(tmp_path / "remote", YOSYS_MISSING)
    config = Planning.local_config(tmp_path, yosys=True)
    async with await remote.connect() as host:
        plan = await Planning.plan(config, [host.tool_host])
    pm, = plan.passes
    assert not isinstance(pm.pass_obj, RemotePass)


async def test_rejections_listed_per_host(tmp_path):
    remote = RemoteSide(tmp_path / "remote", YOSYS_MISSING)
    config = Planning.local_config(tmp_path, yosys=False)
    async with await remote.connect() as host:
        with pytest.raises(PlanningError) as e:
            await Planning.plan(config, [host.tool_host])
    text = str(e.value)
    assert (f"gbs.builtin.yosys/yosys-ice40 on test: tool 'yosys' executable "
            f"{remote.yosys}.missing does not exist") in text
    assert ("gbs.builtin.yosys/yosys-ice40 on local host: "
            "tool 'yosys' not configured on local host") in text


async def test_queries_cached_per_connection(tmp_path):
    remote = RemoteSide(tmp_path / "remote", YOSYS_USABLE)
    config = Planning.local_config(tmp_path, yosys=False)
    async with await remote.connect() as host:
        methods = []
        request = host.peer.request

        async def counting(method, *args, **kwargs):
            methods.append(method)
            return await request(method, *args, **kwargs)

        host.peer.request = counting
        await Planning.plan(config, [host.tool_host])
        queries = methods.count("passes.contribute")
        assert queries > 0
        await Planning.plan(config, [host.tool_host])
        assert methods.count("passes.contribute") == queries


async def test_contribute_errors(tmp_path):
    remote = RemoteSide(tmp_path / "remote", YOSYS_USABLE)
    async with await remote.connect() as host:
        from gbs.remote import RemoteError
        params = {"backend": "gbs.builtin.nothing", "config": {},
                  "requested_types": [], "project_config": {}}
        with pytest.raises(RemoteError) as e:
            await host.peer.request("passes.contribute", params)
        assert e.value.type == "UnknownBackend"
        params["backend"] = "gbs.builtin.yosys"
        params["extra"] = 1
        with pytest.raises(RemoteError) as e:
            await host.peer.request("passes.contribute", params)
        assert e.value.type == "WireError"


class TestPassContribution:
    @staticmethod
    def contribution(**changes):
        fields = dict(
            descriptor=PassDescriptor("b", "p", "m:P", {"k": 1}, ["t"]),
            input_types={"i"}, output_types={"o", "t"}, types_with_library=set(),
            can_fork=False, priority=100, filter_vars={"v": "x"}, problem=None,
        )
        fields.update(changes)
        return PassContribution(**fields)

    def test_round_trip(self):
        original = self.contribution()
        data = json.loads(json.dumps(PassContribution.list_to_json([original])))
        received, = PassContribution.list_from_json(data)
        assert received.to_json() == original.to_json()
        remote = RemotePass("h", received)
        assert remote.filter_vars() == {"v": "x"}
        assert remote.probe() is None
        assert remote.output_types == {"o", "t"}

    def test_problem_excludes_filter_vars(self):
        with pytest.raises(WireError):
            self.contribution(problem="broken")
        with pytest.raises(WireError):
            self.contribution(filter_vars=None)
        rejected = self.contribution(problem="broken", filter_vars=None)
        assert RemotePass("h", rejected).probe() == "broken"

    def test_strict(self):
        data = self.contribution().to_json()
        data["priority"] = True
        with pytest.raises(WireError):
            PassContribution.from_json(data)


class TestCli:
    """`--remote` through a stand-in ssh running the command locally"""

    PROJECT = (
        "name: remote_demo\n"
        "root:\n"
        "  name: top\n"
        "  sources:\n"
        "    - file_type: verilog\n"
        "      files: [top.v]\n"
        "output:\n"
        "  - name: synth\n"
        "    topcell: top\n"
        "    target: {part: iCE40UP5K-SG48I}\n"
        "    outputs:\n"
        "      - {type: ice40-netlist-json, path: top.json}\n"
    )

    @pytest.fixture
    def setup(self, tmp_path):
        remote = RemoteSide(tmp_path / "remote", YOSYS_USABLE)
        local_home = tmp_path / "local"
        (local_home / ".config").mkdir(parents=True)
        (local_home / ".config" / "gbs.yaml").write_text(
            "remote_hosts:\n"
            "  fake:\n"
            "    ssh: [fake.example]\n"
            f"    command: [{sys.executable}, -m, gbs]\n"
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        ssh_log = tmp_path / "ssh.log"
        remote_tmp = tmp_path / "remote-tmp"
        remote_tmp.mkdir()
        ssh = bin_dir / "ssh"
        ssh.write_text(
            "#!/bin/sh\n"
            f"echo \"$@\" >> '{ssh_log}'\n"
            "while [ \"$1\" != \"--\" ]; do shift; done\n"
            "shift\n"
            f"cd '{remote.work}' || exit 1\n"
            f"HOME='{remote.home}' XDG_CACHE_HOME='{remote.cache}' "
            f"TMPDIR='{remote_tmp}' exec sh -c \"$1\"\n"
        )
        ssh.chmod(ssh.stat().st_mode | stat.S_IXUSR)
        project = tmp_path / "project"
        project.mkdir()
        (project / "project.gbs.yaml").write_text(self.PROJECT)
        (project / "top.v").write_text("module top; endmodule\n")
        env = dict(remote.env)
        env.update({
            "HOME": str(local_home),
            "XDG_CACHE_HOME": str(tmp_path / "local-cache"),
            "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
        })

        class Setup:
            @staticmethod
            async def run(*args):
                import asyncio
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "gbs", "-C", str(project), *args,
                    env=env, cwd=str(tmp_path),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                out, err = await asyncio.wait_for(process.communicate(), 120)
                return process.returncode, out.decode(), err.decode()

        Setup.remote = remote
        Setup.remote_tmp = remote_tmp
        Setup.ssh_log = ssh_log
        return Setup

    async def test_build_runs_the_remote_tool(self, setup):
        """The remote yosys is an empty file: running it fails there"""
        code, out, err = await setup.run("project", "build", "--remote", "fake", "--remote-keep")
        assert code == 1
        assert "yosys-ice40 on fake" in out
        assert f"Permission denied: '{setup.remote.yosys}'" in out
        log = setup.ssh_log.read_text()
        assert "fake.example" in log
        assert "remote serve --stdio --keep" in log
        assert list(setup.remote.work.iterdir()) == []
        assert len(list(setup.remote_tmp.glob("gbs-remote-*"))) == 1

    async def test_outputs_plan_remotely(self, setup):
        code, out, err = await setup.run("project", "outputs", "--format", "json", "--remote", "fake")
        assert code == 0, err
        record, = json.loads(out)
        assert record["backends"] == ["gbs.builtin.yosys"]
        assert "error" not in record

        code, out, err = await setup.run("project", "outputs", "--format", "json")
        assert code == 0, err
        record, = json.loads(out)
        assert record["error"].startswith("Cannot find passes")
        assert "backends" not in record

    async def test_remote_keep_needs_remote(self, setup):
        code, out, err = await setup.run("project", "build", "--remote-keep")
        assert code == 2
        assert "--remote-keep requires --remote" in err
        assert not setup.ssh_log.exists()
