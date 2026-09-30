"""Remote segment execution: splitting plans into segments, dispatch
timing, and builds through a remote gbs served by `gbs remote serve
--stdio` in a child process reached through a stand-in ssh"""

import asyncio
import os
import shutil
import signal
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import gbs
from gbs.base import BaseDispatcher
from gbs.build import BuildContext
from gbs.build.task import ConfigurationError
from gbs.planner.passes import PassMetadata
from gbs.remote import (
    Identity, MethodError, PassContribution, PassDescriptor, PlanSegments, PluginCompatibility,
    RemotePass, SegmentRun, WireFormat,
)

SRC = Path(gbs.__file__).resolve().parent.parent
PLUGIN = Path(__file__).resolve().parent / "remote_plugin"
EXTRA = Path(__file__).resolve().parent / "remote_plugin_extra"


class Passes:
    """Pass metadata standing for planned passes"""

    @staticmethod
    def remote(name, inputs, outputs, host="h"):
        contribution = PassContribution(
            PassDescriptor("b", name, "m:C", {}, []), set(inputs), set(outputs),
            set(), False, 100, {}, None)
        return PassMetadata(RemotePass(host, contribution), {}, "b", set())

    @staticmethod
    def local(name, inputs, outputs):
        pass_obj = SimpleNamespace(name=name, input_types=set(inputs), output_types=set(outputs))
        return PassMetadata(pass_obj, {}, "b", set())

    @staticmethod
    def segments(*passes):
        return PlanSegments(SimpleNamespace(passes=list(passes)))

    @staticmethod
    def names(segment):
        return [pm.name for pm in segment.passes]


class TestPlanSegments:
    def test_local_plan_has_no_segment(self):
        split = Passes.segments(Passes.local("a", {"x"}, {"y"}), Passes.local("b", {"y"}, {"x"}))
        assert split.segments == []

    def test_chain_on_one_host_is_one_segment(self):
        c = Passes.remote("c", {"z"}, {"w"})
        a = Passes.remote("a", {"x"}, {"y"})
        b = Passes.remote("b", {"y"}, {"z"})
        split = Passes.segments(c, a, b)
        segment, = split.segments
        assert Passes.names(segment) == ["c", "a", "b"]
        assert segment.upstream == []
        assert split.segment_of(a) is segment
        assert segment.input_types == {"x", "y", "z"}

    def test_local_pass_in_between_splits(self):
        first = Passes.remote("first", {"x"}, {"y"})
        middle = Passes.local("middle", {"y"}, {"z"})
        last = Passes.remote("last", {"z"}, {"w"})
        split = Passes.segments(last, middle, first)
        assert [Passes.names(s) for s in split.segments] == [["last"], ["first"]]
        after, before = split.segments
        assert after.upstream == [before]
        assert before.upstream == []
        assert split.segment_of(middle) is None

    def test_independent_passes_share_their_level(self):
        a = Passes.remote("a", {"x"}, {"y"})
        side = Passes.remote("side", {"x"}, {"q"})
        middle = Passes.local("middle", {"y"}, {"z"})
        after = Passes.remote("after", {"z", "q"}, {"w"})
        split = Passes.segments(a, side, middle, after)
        assert [Passes.names(s) for s in split.segments] == [["a", "side"], ["after"]]
        assert split.segments[1].upstream == [split.segments[0]]

    def test_hosts_are_kept_apart(self):
        a = Passes.remote("a", {"x"}, {"y"}, host="one")
        b = Passes.remote("b", {"y"}, {"z"}, host="two")
        c = Passes.remote("c", {"z"}, {"w"}, host="one")
        split = Passes.segments(a, b, c)
        assert [(s.host, Passes.names(s)) for s in split.segments] == [
            ("one", ["a"]), ("two", ["b"]), ("one", ["c"])]
        assert split.segments[2].upstream == split.segments[:2]

    def test_terminal_aliases_link_passes(self):
        pack = Passes.remote("pack", {"asc"}, {"ice40-bitstream"})
        use = Passes.local("use", {"bitstream"}, {"report"})
        again = Passes.remote("again", {"report"}, {"done"})
        split = Passes.segments(pack, use, again)
        assert len(split.segments) == 2

    def test_loop_is_refused(self):
        with pytest.raises(ConfigurationError, match="loops between passes a, b"):
            Passes.segments(Passes.remote("a", {"x"}, {"y"}), Passes.local("b", {"y"}, {"x"}))


class Producer(BaseDispatcher):
    """Queues one more resource each round, up to a count"""

    def __init__(self, context, count):
        super().__init__(context, "producer", "none")
        self.count = count
        self.made = 0

    async def process(self):
        if self.made < self.count:
            self.made += 1
            path = self.context.base_output_path / f"made{self.made}"
            self.context.add_pending(self.context.get_resource(path, file_type="made"))


class Settled(BaseDispatcher):
    """Records the queue it acts on once settled, and changes it"""

    def __init__(self, context, name, log):
        super().__init__(context, name, "none")
        self.log = log
        self.seen = None

    async def process(self):
        pass

    async def process_settled(self):
        self.log.append(self.name)
        if self.seen is None:
            self.seen = sorted(r.path.name for r in self.context.filter_pending(file_type="made"))
            path = self.context.base_output_path / f"{self.name}.out"
            self.context.add_pending(self.context.get_resource(path, file_type="settled"))


class TestSettledDispatch:
    async def test_waits_for_every_round_of_production(self, tmp_path):
        ctx = BuildContext(base_output_path=tmp_path)
        log = []
        ctx.register_dispatcher(first := Settled(ctx, "first", log))
        ctx.register_dispatcher(Producer(ctx, 3))
        ctx.register_dispatcher(second := Settled(ctx, "second", log))

        iterations = await ctx.run_dispatcher_iteration()

        assert first.seen == second.seen == ["made1", "made2", "made3"]
        # One settled dispatcher acts per settled queue, then rounds resume
        assert log == ["first", "first", "second", "first", "second"]
        assert iterations == 6

    async def test_converges_without_settled_work(self, tmp_path):
        ctx = BuildContext(base_output_path=tmp_path)
        ctx.register_dispatcher(Producer(ctx, 2))
        assert await ctx.run_dispatcher_iteration() == 3


class RemoteBuild:
    """A project using the remotetest plugin, built through a stand-in ssh

    The local host runs rt-prep and rt-use, the remote host rt-gen, so
    every build goes local, remote, then local again.

    Args:
        root: Directory everything lives in
        local_plugins: Plugin directories of the local host
        remote_plugins: Plugin directories of the remote host
        local_tools: Tools configured on the local host
    """

    PROJECT = (
        "name: rt\n"
        "root:\n"
        "  name: top\n"
        "  sources:\n"
        "    - file_type: rt-raw\n"
        "      files: [a.raw, b.raw, c.raw]\n"
        "output:\n"
        "  - name: og\n"
        "    topcell: top\n"
        "    outputs:\n"
        "      - {type: rt-out, path: out.txt}\n"
        "      - {type: rt-report, path: report.txt}\n"
    )

    def __init__(self, root: Path, local_plugins=(PLUGIN,), remote_plugins=(PLUGIN,),
                 local_tools=("rtprep", "rtuse")):
        self.root = root
        self.remote_home = root / "remote" / "home"
        (self.remote_home / ".config").mkdir(parents=True)
        (self.remote_home / ".config" / "gbs.yaml").write_text("tools:\n  - name: rtgen\n")
        self.remote_work = root / "remote" / "work"
        self.remote_work.mkdir()
        self.remote_cache = root / "remote" / "cache"
        self.remote_tmp = root / "remote" / "tmp"
        self.remote_tmp.mkdir()

        self.local_home = local_home = root / "local"
        (local_home / ".config").mkdir(parents=True)
        (local_home / ".config" / "gbs.yaml").write_text(
            "tools:\n"
            + "".join(f"  - name: {tool}\n" for tool in local_tools)
            + "remote_hosts:\n"
            "  fake:\n"
            "    ssh: [fake.example]\n"
            f"    command: [{sys.executable}, -m, gbs]\n"
        )
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.ssh_log = root / "ssh.log"
        ssh = bin_dir / "ssh"
        ssh.write_text(
            "#!/bin/sh\n"
            f"echo \"$@\" >> '{self.ssh_log}'\n"
            "while [ \"$1\" != \"--\" ]; do shift; done\n"
            "shift\n"
            f"cd '{self.remote_work}' || exit 1\n"
            f"HOME='{self.remote_home}' XDG_CACHE_HOME='{self.remote_cache}' "
            f"TMPDIR='{self.remote_tmp}' PYTHONPATH='{self.pythonpath(remote_plugins)}' "
            "exec sh -c \"$1\"\n"
        )
        ssh.chmod(ssh.stat().st_mode | stat.S_IXUSR)

        self.project = root / "project"
        self.project.mkdir()
        (self.project / "project.gbs.yaml").write_text(self.PROJECT)
        self.raw_write(a="alpha", b="beta", c="gamma")
        self.env = dict(os.environ)
        self.env.update({
            "HOME": str(local_home),
            "XDG_CACHE_HOME": str(root / "local-cache"),
            "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
            "PYTHONPATH": self.pythonpath(local_plugins),
        })

    @staticmethod
    def pythonpath(plugins) -> str:
        # A different HOME hides user site-packages: pass the whole path
        return os.pathsep.join([str(p) for p in plugins] + [str(SRC)] + [p for p in sys.path if p])

    def exclude(self, *dispatchers):
        (self.project / "project.gbs.yaml").write_text(
            self.PROJECT + f"    exclude_dispatchers: [{', '.join(dispatchers)}]\n")

    def raw_write(self, **contents):
        for name, text in contents.items():
            (self.project / f"{name}.raw").write_text(text + "\n")

    async def start(self, *args):
        return await asyncio.create_subprocess_exec(
            sys.executable, "-m", "gbs", "-P", "-C", str(self.project), *args,
            env=self.env, cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

    async def run(self, *args):
        process = await self.start(*args)
        out, err = await asyncio.wait_for(process.communicate(), 120)
        return process.returncode, out.decode(), err.decode()

    async def build(self, *options, keep=False):
        """Build, with options of the gbs command itself"""
        keep_args = ["--remote-keep"] if keep else []
        return await self.run(*options, "project", "build", "--remote", "fake", *keep_args)

    def output(self, name):
        return self.project / "gbs-build" / "og" / name

    def workspaces(self):
        return list(self.remote_tmp.glob("gbs-remote-*"))


@pytest.fixture
def rt(tmp_path):
    return RemoteBuild(tmp_path)


class TestRemoteBuild:
    async def test_local_remote_local(self, rt):
        code, out, err = await rt.build("-v")
        assert code == 0, out + err

        assert (rt.project / "out.txt").read_text() == (
            "ALPHA\nBETA\nGAMMA\n"
            "d0\nd0/alpha.txt\nd1\nd1/beta.txt\nd2\nd2/gamma.txt\nempty\ntool.sh\n"
        )
        assert (rt.project / "report.txt").read_text() == f"home {rt.remote_home}\n"
        tree = rt.output("tree")
        assert (tree / "d1" / "beta.txt").read_text() == "beta"
        assert (tree / "empty").is_dir() and not any((tree / "empty").iterdir())
        assert os.access(tree / "tool.sh", os.X_OK)
        assert not os.access(tree / "d0" / "alpha.txt", os.X_OK)
        assert not list(rt.output("").glob(".remote-blobs-*"))
        assert rt.workspaces() == []
        assert list(rt.remote_work.iterdir()) == []
        assert "Sent 3 blob(s) to fake" in err + out

        stamp = (rt.project / "out.txt").stat().st_mtime_ns
        code, out, err = await rt.build("-v")
        assert code == 0, out + err
        assert "Task remote-segment-0: up-to-date, skipping" in err + out
        assert "Sent " not in err + out
        assert (rt.project / "out.txt").stat().st_mtime_ns == stamp

    async def test_directory_output_is_replaced(self, rt):
        code, out, err = await rt.build()
        assert code == 0, out + err
        rt.raw_write(a="delta")
        code, out, err = await rt.build()
        assert code == 0, out + err
        tree = rt.output("tree")
        assert (tree / "d0" / "delta.txt").is_file()
        assert not (tree / "d0" / "alpha.txt").exists()
        assert not list(tree.parent.glob(".tree.*"))
        assert (rt.project / "out.txt").read_text().startswith("DELTA\nBETA\n")

    async def test_workspace_kept_on_request(self, rt):
        code, out, err = await rt.build(keep=True)
        assert code == 0, out + err
        workspace, = rt.workspaces()
        segment, = workspace.iterdir()
        assert (segment / "roots" / "project" / "gbs-build" / "og" / "mid.txt").is_file()

    async def test_failure_is_reported(self, rt):
        rt.raw_write(b="FAIL")
        code, out, err = await rt.build()
        assert code == 1
        text = out + err
        assert "✗ remote-segment-0" in text
        assert "rt-gen on fake" in text
        assert "Reason: fake: rt-gen: generator refuses FAIL" in text
        assert f"{rt.output('src')}/a.src:1:error: generator refuses FAIL" in text
        # The remote failure summary, below the local one
        assert "✗ rt-gen" in text and "Reason: generation failed" in text
        assert not (rt.project / "out.txt").exists()
        assert rt.workspaces() == []

    async def test_cancellation_reaches_the_remote(self, rt):
        marker = rt.root / "marker"
        rt.raw_write(b=f"HANG {marker}")
        process = await rt.start("project", "build", "--remote", "fake")
        try:
            async with asyncio.timeout(60):
                while not marker.exists():
                    await asyncio.sleep(0.1)
            process.send_signal(signal.SIGINT)
            await asyncio.wait_for(process.communicate(), 60)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode != 0
        async with asyncio.timeout(30):
            while rt.workspaces():
                await asyncio.sleep(0.1)
        assert Path(f"{marker}.cancelled").read_text() == "cancelled"

    async def test_segment_waits_for_local_producers(self, rt):
        code, out, err = await rt.build("-d")
        assert code == 0, out + err
        lines = (out + err).splitlines()
        settled = next(i for i, line in enumerate(lines)
                       if "remote-segment-0 acted on the settled queue" in line)
        prepared = [i for i, line in enumerate(lines) if "Running dispatcher: rt-prep" in line]
        # rt-prep produces one source per round; the segment only
        # acts after it stopped
        assert len([i for i in prepared if i < settled]) >= 4


class PluginTrees:
    """Plugin directories for one host only"""

    @staticmethod
    def modified(root: Path) -> Path:
        """The remotetest plugin, same version, other sources"""
        tree = root / "modified-plugin"
        shutil.copytree(PLUGIN, tree, ignore=shutil.ignore_patterns("__pycache__"))
        init = tree / "gbs" / "plugin" / "remotetest" / "__init__.py"
        init.write_text(init.read_text() + "\n# modified\n")
        return tree

    @staticmethod
    def versioned(root: Path) -> Path:
        """The remoteextra plugin, another version"""
        tree = root / "versioned-plugin"
        shutil.copytree(EXTRA, tree, ignore=shutil.ignore_patterns("__pycache__"))
        init = tree / "gbs" / "plugin" / "remoteextra" / "__init__.py"
        init.write_text(init.read_text().replace('version="0.0.1"', 'version="0.0.2"'))
        return tree

    @staticmethod
    def empty(root: Path, name: str) -> Path:
        """A plugin providing nothing"""
        tree = root / f"{name}-plugin"
        package = tree / "gbs" / "plugin" / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(
            "from gbs.base import BasePlugin\n\n\n"
            "def gbs_register():\n"
            f"    return BasePlugin(name='gbs.plugin.{name}', version='3.0')\n"
        )
        return tree


class TestPluginCompatibility:
    async def test_local_generic_dispatchers_skipped_remotely(self, tmp_path):
        rt = RemoteBuild(tmp_path, local_plugins=(PLUGIN, EXTRA))
        code, out, err = await rt.build()
        assert code == 0, out + err
        assert ("rt-gen on fake: skipping dispatchers rt-extra: "
                "plugin gbs.plugin.remoteextra is not installed on fake") in out + err
        assert (rt.project / "report.txt").read_text() == f"home {rt.remote_home}\n"
        assert (rt.local_home / "rt-extra-ran").exists()
        assert not (rt.remote_home / "rt-extra-ran").exists()

    async def test_remote_generic_dispatchers_skipped(self, tmp_path):
        rt = RemoteBuild(tmp_path, remote_plugins=(PLUGIN, EXTRA))
        code, out, err = await rt.build()
        assert code == 0, out + err
        assert (rt.project / "report.txt").read_text() == f"home {rt.remote_home}\n"
        assert not (rt.remote_home / "rt-extra-ran").exists()

    async def test_generic_plugin_versions_differ(self, tmp_path):
        rt = RemoteBuild(tmp_path, local_plugins=(PLUGIN, EXTRA),
                         remote_plugins=(PLUGIN, PluginTrees.versioned(tmp_path)))
        code, out, err = await rt.build()
        assert code == 0, out + err
        assert ("rt-gen on fake: skipping dispatchers rt-extra: "
                "plugin gbs.plugin.remoteextra version 0.0.1 here, 0.0.2 on fake") in out + err
        assert (rt.local_home / "rt-extra-ran").exists()
        assert not (rt.remote_home / "rt-extra-ran").exists()

    async def test_backend_plugin_sources_differ(self, tmp_path):
        modified = PluginTrees.modified(tmp_path)
        rt = RemoteBuild(tmp_path / "remote-only", remote_plugins=(modified,))
        code, out, err = await rt.build()
        assert code == 1
        assert ("gbs.plugin.remotetest on fake: "
                "plugin gbs.plugin.remotetest sources differ on fake") in out + err

        rt = RemoteBuild(tmp_path / "fallback", remote_plugins=(modified,),
                         local_tools=("rtprep", "rtgen", "rtuse"))
        code, out, err = await rt.build()
        assert code == 0, out + err
        assert (rt.project / "report.txt").read_text() != f"home {rt.remote_home}\n"
        assert "remote-segment" not in out + err

    async def test_remote_info(self, tmp_path):
        rt = RemoteBuild(
            tmp_path, local_plugins=(PLUGIN, EXTRA, PluginTrees.empty(tmp_path, "localonly")),
            remote_plugins=(PluginTrees.modified(tmp_path), PluginTrees.versioned(tmp_path),
                            PluginTrees.empty(tmp_path, "remoteonly")))
        code, out, err = await rt.run("remote", "info", "fake")
        assert code == 0, out + err
        lines = out.splitlines()
        assert "  gbs.builtin.compress 1.0.0  # same" in lines
        assert "  gbs.plugin.remoteextra 0.0.2  # version differs, 0.0.1 on local host" in lines
        assert "  gbs.plugin.remoteonly 3.0  # not installed on local host" in lines
        assert "  gbs.plugin.localonly  # not installed on fake, 3.0 on local host" in lines
        index = lines.index("  gbs.plugin.remotetest 0.0.1  # sources differ")
        assert lines[index + 1] == "    __init__.py: differs"
        assert lines.index("tools:") > index
        assert "  rtgen" in lines

    def test_segment_generic_dispatchers_selected_remotely(self):
        mine = Identity(WireFormat.VERSION, "1.0", {"a": "1", "b": "1"}, {"gbs": "g", "a": "a", "b": "b"})
        theirs = Identity(WireFormat.VERSION, "1.0", {"a": "1", "c": "1"}, {"gbs": "g", "a": "a", "c": "c"})
        dispatcher = SimpleNamespace(name="d")

        def select(listed, generic):
            run = SimpleNamespace(
                descriptor=SimpleNamespace(generic_plugins=frozenset(listed)),
                compatibility=PluginCompatibility(mine, theirs, "client", True))
            return SegmentRun.generic_select(run, {name: [dispatcher] for name in generic})

        assert select({"a"}, {"a"}) == {"a": [dispatcher]}
        assert select({"a"}, {"a", "b"}) == {"a": [dispatcher]}
        assert select(set(), {"a"}) == {}
        with pytest.raises(MethodError) as e:
            select({"a", "c"}, {"a"})
        assert e.value.type == "IncompatiblePlugin"
        assert str(e.value) == ("plugin c is not installed here, "
                                "but client registers its generic dispatchers")
        with pytest.raises(MethodError) as e:
            select({"a", "b"}, {"a", "b"})
        assert str(e.value) == ("plugin b is not installed on client, "
                                "but client registers its generic dispatchers")
