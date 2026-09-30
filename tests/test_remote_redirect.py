"""Tools redirected to a remote host by the configuration"""

import os
import sys
from pathlib import Path

import pytest
import asyncclick as click

from gbs.build import BuildContext
from gbs.build.task import ConfigurationError
from gbs.builtin.tcl_interp import TclInterpreter
from gbs.cli.openxc7 import _resolve_install_root
from gbs.config.model import ConfigError, GBSConfig, ToolConfig, ToolRedirected
from gbs.planner.planner import PlanningError
from gbs.remote import RedirectDisabled, RemotePass

from .test_remote_execution import RemoteBuild
from .test_remote_planning import YOSYS_USABLE, Planning, RemoteSide


class TestConfig:
    @staticmethod
    def parse(tmp_path, text):
        path = tmp_path / "gbs.yaml"
        path.write_text(text)
        return GBSConfig._parse_config_file(path)

    def test_remote_parsed(self, tmp_path):
        config = self.parse(tmp_path, (
            "tools:\n"
            "  - name: vivado\n"
            "    remote: lure\n"
            "  - name: vivado\n"
            "    variant: '2022.2'\n"
            "    remote: nipo@lure\n"
            "    config: {env: {A: b}}\n"
            "  - name: yosys\n"
        ))
        assert [t.remote for t in config.tools] == ["lure", "nipo@lure", None]
        assert config.get_tool("vivado:2022.2").config == {"env": {"A": "b"}}

    @pytest.mark.parametrize("entry, message", [
        ("remote: lure\n    config: {path: /opt/vivado}", "redirected to lure and cannot declare path"),
        ("remote: lure\n    config: {executable: v, path: p}",
         "cannot declare path or executable"),
        ("remote: [lure]", "remote must be a non-empty string"),
        ("remote: 3", "remote must be a non-empty string"),
        ("remote: ''", "remote must be a non-empty string"),
    ])
    def test_remote_invalid(self, tmp_path, entry, message):
        with pytest.raises(ConfigError, match=message):
            self.parse(tmp_path, f"tools:\n  - name: vivado\n    {entry}\n")

    def test_override(self, tmp_path):
        base = GBSConfig(tools=[ToolConfig("vivado", None, None, {"path": "/opt/vivado"})])
        redirected = GBSConfig(tools=[ToolConfig("vivado", remote="lure")])
        merged = GBSConfig._merge_configs(base, redirected)
        tool, = merged.tools
        assert tool.remote == "lure" and tool.config == {}
        tool, = GBSConfig._merge_configs(merged, base).tools
        assert tool.remote is None


class TestLocalUse:
    CONFIG = GBSConfig(tools=[ToolConfig("vivado", remote="lure"), ToolConfig("tclsh", remote="lure")])
    MESSAGE = "tool 'vivado' is redirected to host lure; this command runs tools locally"

    def test_accessor(self):
        with pytest.raises(ToolRedirected, match=self.MESSAGE):
            self.CONFIG.get_tool("vivado").local()
        tool = ToolConfig("yosys")
        assert tool.local() is tool

    def test_build_context(self, tmp_path):
        ctx = BuildContext(base_output_path=tmp_path, gbs_config=self.CONFIG)
        for required in (True, False):
            with pytest.raises(ConfigurationError, match=self.MESSAGE):
                ctx.get_tool("vivado", required=required)

    def test_cli_command(self):
        with pytest.raises(click.ClickException, match=self.MESSAGE):
            _resolve_install_root(self.CONFIG, "vivado")

    def test_tcl_interpreter_not_taken(self, monkeypatch):
        monkeypatch.setenv("PATH", "")
        assert TclInterpreter.resolve(self.CONFIG, "tclsh") is None


class TestPlanner:
    """An ice40 synthesis whose yosys is redirected"""

    CONFIG = GBSConfig(tools=[ToolConfig("yosys", remote="test")])

    async def test_without_provider(self):
        with pytest.raises(PlanningError) as e:
            await Planning.plan(self.CONFIG, [])
        assert ("gbs.builtin.yosys/yosys-ice40 on local host: "
                "tool 'yosys' is provided by host test") in str(e.value)

    async def test_disabled(self):
        planner = Planning.planner(self.CONFIG, [])

        async def disabled(destination):
            raise RedirectDisabled("not today")

        planner.redirect_host = disabled
        with pytest.raises(PlanningError) as e:
            await planner.plan(Planning.output_group())
        assert ("gbs.builtin.yosys/yosys-ice40 on local host: "
                "tool 'yosys' is provided by host test; not today") in str(e.value)

    async def test_connected_once(self, tmp_path):
        remote = RemoteSide(tmp_path / "remote", YOSYS_USABLE)
        destinations = []
        async with await remote.connect() as host:
            async def provider(destination):
                destinations.append(destination)
                return host.tool_host

            planner = Planning.planner(self.CONFIG, [])
            planner.redirect_host = provider
            plan = await planner.plan(Planning.output_group())
        pm, = plan.passes
        assert isinstance(pm.pass_obj, RemotePass)
        assert pm.pass_obj.host == "test"
        assert set(destinations) == {"test"}

    async def test_authoritative_over_remote(self, tmp_path):
        """The redirect host is used even when another remote offers the pass"""
        remote = RemoteSide(tmp_path / "remote", YOSYS_USABLE)
        async with await remote.connect() as host:
            async def provider(destination):
                raise RedirectDisabled("unreachable")

            config = GBSConfig(tools=[ToolConfig("yosys", remote="elsewhere")])
            planner = Planning.planner(config, [host.tool_host])
            planner.redirect_host = provider
            with pytest.raises(PlanningError) as e:
                await planner.plan(Planning.output_group())
        assert "tool 'yosys' is provided by host elsewhere; unreachable" in str(e.value)
        assert "yosys-ice40 on test" not in str(e.value)


class Redirected(RemoteBuild):
    """The remotetest build, rt-gen redirected by the local configuration

    Args:
        root: See RemoteBuild
        redirect: Tools of the local configuration redirected, by
            destination
        local_tools: Tools of the local configuration
    """

    def __init__(self, root: Path, redirect: dict[str, str],
                 local_tools=("rtprep", "rtuse")):
        super().__init__(root)
        (self.local_home / ".config" / "gbs.yaml").write_text(
            "tools:\n"
            + "".join(f"  - name: {tool}\n" for tool in local_tools)
            + "".join(f"  - name: {tool}\n    remote: {dest}\n" for tool, dest in redirect.items())
            + "remote_hosts:\n"
            + "".join(
                f"  {name}:\n"
                f"    ssh: [{name}.example]\n"
                f"    command: [{sys.executable}, -m, gbs]\n"
                for name in ("fake", "other")
            )
        )

    def ssh_destinations(self) -> list[str]:
        if not self.ssh_log.exists():
            return []
        return [line.split()[1] for line in self.ssh_log.read_text().splitlines()]


class TestRedirectedBuild:
    async def test_build_goes_remote(self, tmp_path):
        rt = Redirected(tmp_path, {"rtgen": "fake"})
        code, out, err = await rt.run("-v", "project", "build")
        assert code == 0, out + err
        assert (rt.project / "report.txt").read_text() == f"home {rt.remote_home}\n"
        assert "Sent 3 blob(s) to fake" in out + err
        assert rt.ssh_destinations() == ["fake.example"]
        assert rt.workspaces() == []

    async def test_unused_redirect_connects_nothing(self, tmp_path):
        rt = Redirected(tmp_path, {"vivado": "fake"}, local_tools=("rtprep", "rtgen", "rtuse"))
        code, out, err = await rt.run("project", "build")
        assert code == 0, out + err
        assert (rt.project / "report.txt").read_text() == f"home {Path(rt.env['HOME'])}\n"
        assert rt.ssh_destinations() == []

    async def test_config_dump(self, tmp_path):
        rt = Redirected(tmp_path, {"rtgen": "fake"})
        code, out, err = await rt.run("config", "dump")
        assert code == 0, out + err
        lines = out.splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("  - name: rtgen"))
        assert lines[index + 1] == "    remote: fake"

    async def test_no_remote(self, tmp_path):
        rt = Redirected(tmp_path, {"rtgen": "fake"})
        code, out, err = await rt.run("project", "build", "--no-remote")
        assert code == 1
        assert ("gbs.plugin.remotetest/rt-gen on local host: tool 'rtgen' is provided by "
                "host fake; redirection disabled by --no-remote") in out + err
        assert rt.ssh_destinations() == []

    async def test_no_remote_excludes_remote(self, tmp_path):
        rt = Redirected(tmp_path, {"rtgen": "fake"})
        code, out, err = await rt.run("project", "build", "--no-remote", "--remote", "fake")
        assert code == 2
        assert "--no-remote and --remote are exclusive" in err
        assert rt.ssh_destinations() == []

    async def test_redirect_wins_over_remote(self, tmp_path):
        rt = Redirected(tmp_path, {"rtgen": "other"})
        code, out, err = await rt.run("-v", "project", "build", "--remote", "fake")
        assert code == 0, out + err
        assert "Sent 3 blob(s) to other" in out + err
        assert "to fake" not in out + err
        assert sorted(rt.ssh_destinations()) == ["fake.example", "other.example"]

    async def test_redirect_host_rejects(self, tmp_path):
        rt = Redirected(tmp_path, {"rtgen": "fake"})
        (rt.remote_home / ".config" / "gbs.yaml").write_text("tools: []\n")
        code, out, err = await rt.run("project", "build")
        assert code == 1
        text = out + err
        assert ("gbs.plugin.remotetest/rt-gen on local host: "
                "tool 'rtgen' is provided by host fake") in text
        assert "gbs.plugin.remotetest/rt-gen on fake: tool 'rtgen' not configured" in text
        assert rt.ssh_destinations() == ["fake.example"]
        assert rt.workspaces() == []
