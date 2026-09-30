"""Tests for the remote framing, messaging and tool hosts"""

import asyncio
import hashlib
import importlib
import socket
import struct
import sys
from pathlib import Path

import pytest
from asyncclick.testing import CliRunner

import gbs
from gbs.cli.config import config as config_cli
from gbs.config.model import ConfigError, GBSConfig, ToolConfig
from gbs.remote import (
    ChannelClosed, Frame, FrameChannel, FrameError, Identity, LocalToolHost,
    MethodError, Peer, RemoteError, RemoteHost, RemoteToolHost, Reply,
    PluginCompatibility, SourceDigest, SourceFiles, ToolDescription, HandshakeError, WireError,
    WireFormat,
)


async def channel_pair(**limits):
    a, b = socket.socketpair()
    ra, wa = await asyncio.open_connection(sock=a)
    rb, wb = await asyncio.open_connection(sock=b)
    return FrameChannel(ra, wa, **limits), FrameChannel(rb, wb, **limits)


def fed_channel(data: bytes, **limits) -> FrameChannel:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return FrameChannel(reader, None, **limits)


class TestFraming:
    async def test_round_trip(self):
        a, b = await channel_pair()
        await a.write(Frame({"x": [1, "é", None]}))
        await a.write(Frame({"y": True}, b"\x00\x01binary\xff"))
        await a.write(Frame({}, b""))
        assert await b.read() == Frame({"x": [1, "é", None]})
        assert await b.read() == Frame({"y": True}, b"\x00\x01binary\xff")
        assert await b.read() == Frame({}, b"")
        await a.close()
        assert await b.read() is None
        await b.close()

    def test_layout(self):
        data = FrameChannel(None, None).encode(Frame({"k": 1}, b"ab"))
        assert data == struct.pack(">I", 7) + b'{"k":1}' + struct.pack(">I", 2) + b"ab"

    async def test_oversize_refused_on_write(self):
        a, b = await channel_pair(header_max=32, body_max=4)
        with pytest.raises(FrameError, match="header"):
            await a.write(Frame({"k": "x" * 40}))
        with pytest.raises(FrameError, match="body"):
            await a.write(Frame({}, b"12345"))
        await a.close()
        assert await b.read() is None
        await b.close()

    async def test_oversize_refused_on_read(self):
        big = FrameChannel(None, None).encode(Frame({"k": "x" * 40}, b"12345"))
        with pytest.raises(FrameError, match="header of 48 bytes"):
            await fed_channel(big, header_max=32).read()
        with pytest.raises(FrameError, match="body of 5 bytes"):
            await fed_channel(big, body_max=4).read()

    async def test_non_json_refused(self):
        a, b = await channel_pair()
        with pytest.raises(FrameError, match="plain JSON"):
            await a.write(Frame({"k": object()}))
        with pytest.raises(FrameError, match="plain JSON"):
            await a.write(Frame({"k": float("nan")}))
        await a.close()
        await b.close()
        with pytest.raises(FrameError, match="valid JSON"):
            await fed_channel(struct.pack(">I", 3) + b"{x}" + struct.pack(">I", 0)).read()
        with pytest.raises(FrameError, match="must be an object"):
            await fed_channel(struct.pack(">I", 2) + b"[]" + struct.pack(">I", 0)).read()

    @pytest.mark.parametrize("cut", [1, 4, 6, 12, 14])
    async def test_truncated(self, cut):
        data = FrameChannel(None, None).encode(Frame({"k": 1}, b"ab"))
        with pytest.raises(FrameError, match="Stream ends within frame"):
            await fed_channel(data[:cut]).read()

    async def test_clean_end(self):
        data = FrameChannel(None, None).encode(Frame({"k": 1}))
        channel = fed_channel(data)
        assert await channel.read() == Frame({"k": 1})
        assert await channel.read() is None


async def peer_pair():
    a, b = await channel_pair()
    return Peer(a, "a"), Peer(b, "b")


class TestPeer:
    async def test_concurrent_both_directions(self):
        a, b = await peer_pair()
        release = asyncio.Event()

        async def slow(call):
            await release.wait()
            return {"slow": call.params}

        async def echo(call):
            return Reply({"echo": call.params}, call.body[::-1])

        async def ask_back(call):
            reply = await call.peer.request("echo", call.params + 1)
            return reply.result

        for peer in (a, b):
            peer.method_register("slow", slow)
            peer.method_register("echo", echo)
            peer.method_register("ask_back", ask_back)

        async with a, b:
            slow_a = asyncio.create_task(a.request("slow", 1))
            slow_b = asyncio.create_task(b.request("slow", 2))
            await asyncio.sleep(0)
            fast = await asyncio.gather(*(
                (a if i % 2 else b).request("echo", i, body=bytes([i, 0]))
                for i in range(10)
            ))
            assert [r.result for r in fast] == [{"echo": i} for i in range(10)]
            assert [r.body for r in fast] == [bytes([0, i]) for i in range(10)]
            assert (await a.request("ask_back", 5)).result == {"echo": 6}
            assert not slow_a.done() and not slow_b.done()
            release.set()
            assert (await slow_a).result == {"slow": 1}
            assert (await slow_b).result == {"slow": 2}

    async def test_errors(self):
        a, b = await peer_pair()

        async def fails(call):
            raise ValueError("bad value")

        async def refuses(call):
            raise MethodError("Refused", "not now", {"why": [1]})

        async def unencodable(call):
            return {"x": object()}

        b.method_register("fails", fails)
        b.method_register("refuses", refuses)
        b.method_register("unencodable", unencodable)
        async with a, b:
            with pytest.raises(RemoteError) as e:
                await a.request("fails")
            assert (e.value.type, e.value.message) == ("ValueError", "bad value")
            with pytest.raises(RemoteError) as e:
                await a.request("refuses")
            assert (e.value.type, e.value.message, e.value.data) == ("Refused", "not now", {"why": [1]})
            with pytest.raises(RemoteError) as e:
                await a.request("nothing")
            assert e.value.type == "UnknownMethod"
            with pytest.raises(RemoteError) as e:
                await a.request("unencodable")
            assert e.value.type == "FrameError"
            assert a.closed is None and b.closed is None

    async def test_events(self):
        a, b = await peer_pair()

        async def progress(call):
            for i in range(3):
                await call.event("step", {"n": i}, bytes([i]))
            return "done"

        b.method_register("progress", progress)
        unsolicited = []
        a.subscribe("notice", unsolicited.append)
        async with a, b:
            seen = []
            reply = await a.request("progress", on_event=seen.append)
            assert reply.result == "done"
            assert [(e.name, e.data, e.body) for e in seen] == [
                ("step", {"n": i}, bytes([i])) for i in range(3)
            ]
            assert {e.request for e in seen} == {seen[0].request}
            await b.event("notice", [1, 2])
            await b.event("unheard")
            await a.request("progress")
            assert [(e.name, e.data, e.request) for e in unsolicited] == [("notice", [1, 2], None)]

    async def test_cancel_propagates(self):
        a, b = await peer_pair()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hang(call):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def echo(call):
            return call.params

        b.method_register("hang", hang)
        b.method_register("echo", echo)
        async with a, b:
            task = asyncio.create_task(a.request("hang"))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cancelled.wait(), 5)
            assert (await a.request("echo", 3)).result == 3
            assert a.closed is None and b.closed is None

    async def test_connection_loss_fails_pending(self):
        a, b = await peer_pair()
        started = asyncio.Event()
        handler_cancelled = asyncio.Event()

        async def hang(call):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                handler_cancelled.set()
                raise

        b.method_register("hang", hang)
        a.method_register("hang", hang)
        a.start()
        b.start()
        pending = [asyncio.create_task(a.request("hang")) for _ in range(3)]
        served = asyncio.create_task(b.request("hang"))
        await started.wait()
        await asyncio.sleep(0.05)
        await b.close()
        for task in pending:
            with pytest.raises(ChannelClosed):
                await task
        with pytest.raises(ChannelClosed):
            await served
        reason = await asyncio.wait_for(a.wait_closed(), 5)
        assert "closed by the other side" in str(reason)
        await asyncio.wait_for(handler_cancelled.wait(), 5)
        with pytest.raises(ChannelClosed):
            await a.request("hang")
        await a.close()

    async def test_protocol_error_closes(self):
        a_channel, b_channel = await channel_pair()
        a = Peer(a_channel, "a")
        a.start()
        pending = asyncio.create_task(a.request("anything"))
        await b_channel.read()
        await b_channel.write(Frame({"kind": "response", "id": 99, "result": None}))
        with pytest.raises(ChannelClosed, match="unknown request 99"):
            await pending
        await a.close()
        await b_channel.close()

    async def test_shutdown_after_reply(self):
        a, b = await peer_pair()

        async def shutdown(call):
            call.closing()
            return "bye"

        b.method_register("shutdown", shutdown)
        async with a:
            b.start()
            assert (await a.request("shutdown")).result == "bye"
            await asyncio.wait_for(a.wait_closed(), 5)
            assert b.closed is not None


class TestIdentity:
    def test_gbs_mismatch_refused_plugins_left(self):
        mine = Identity(WireFormat.VERSION, "1.0", {"p": "1", "q": "2"},
                        {"gbs": "aa", "p": "11", "q": "22"})
        theirs = Identity(WireFormat.VERSION, "1.1", {"p": "1", "q": "3", "r": "1"},
                          {"gbs": "ab", "p": "11", "q": "23", "r": "33"})
        with pytest.raises(HandshakeError) as e:
            mine.check(theirs, "local host", "srv")
        text = str(e.value)
        assert "gbs version: 1.0 on local host, 1.1 on srv" in text
        assert "gbs sources: aa on local host, ab on srv" in text
        assert "plugin" not in text
        plugins = [(d.what, d.mine, d.theirs, d.plugin)
                   for d in mine.differences(theirs) if d.plugin is not None]
        assert plugins == [
            ("plugin q", "2", "3", "q"),
            ("plugin r", "not installed", "1", "r"),
            ("plugin q sources", "22", "23", "q"),
            ("plugin r sources", "not installed", "33", "r"),
        ]
        plugins_only = Identity(WireFormat.VERSION, "1.0", {"p": "2"}, {"gbs": "aa", "p": "12"})
        assert mine.check(plugins_only, "a", "b") == []

    def test_unchecked_sources(self):
        mine = Identity(WireFormat.VERSION, "1.0", {"p": "1"}, {"gbs": "aa", "p": "11"})
        sources = Identity(WireFormat.VERSION, "1.0", {"p": "1"}, {"gbs": "ab", "p": "12"})
        assert mine.check(sources, "here", "srv", check_sources=False) == [
            "  gbs sources: aa on here, ab on srv"]
        with pytest.raises(HandshakeError, match="gbs sources: aa on here, ab on srv"):
            mine.check(sources, "here", "srv")
        version = Identity(WireFormat.VERSION, "1.1", {"p": "1"}, {"gbs": "ab", "p": "11"})
        with pytest.raises(HandshakeError) as e:
            mine.check(version, "here", "srv", check_sources=False)
        assert "gbs version: 1.0 on here, 1.1 on srv" in str(e.value)
        assert "gbs sources: aa on here, ab on srv" in str(e.value)

    def test_file_differences(self):
        mine = Identity(WireFormat.VERSION, "1.0", {"p": "1"}, {"gbs": "aa", "p": "11"})
        theirs = Identity(WireFormat.VERSION, "1.0", {"p": "1"}, {"gbs": "ab", "p": "12"})
        common = {f"m{i:02}.py": "0" for i in range(20)}
        files = {
            "gbs": ({**common, "a.py": "1", "b.py": "2", "local.py": "3"},
                    {**common, "a.py": "1", "b.py": "9", "far/remote.py": "4"}),
            "p": ({f"x{i:02}.py": "1" for i in range(12)},
                  {f"x{i:02}.py": "2" for i in range(12)}),
        }
        with pytest.raises(HandshakeError) as e:
            mine.check(theirs, "local host", "srv", files=files)
        assert str(e.value).split("\n")[1:] == [
            "  gbs sources: aa on local host, ab on srv",
            "    b.py: differs",
            "    far/remote.py: only on srv",
            "    local.py: only on local host",
        ]
        mine, theirs = files["p"]
        assert SourceFiles.describe(mine, theirs, "local host", "srv") == [
            *(f"x{i:02}.py: differs" for i in range(10)),
            "and 2 more file(s)",
        ]


class TestPluginCompatibility:
    MINE = Identity(WireFormat.VERSION, "1.0", {"same": "1", "version": "1", "sources": "1", "local": "1"},
                    {"gbs": "aa", "same": "s1", "version": "v1", "sources": "x1", "local": "l1"})
    THEIRS = Identity(WireFormat.VERSION, "1.0", {"same": "1", "version": "2", "sources": "1", "remote": "1"},
                      {"gbs": "aa", "same": "s1", "version": "v2", "sources": "x2", "remote": "r1"})

    def test_problems(self):
        compatibility = PluginCompatibility(self.MINE, self.THEIRS, "srv", True)
        assert compatibility.problem("same") is None
        assert compatibility.problems(["same", "version", "sources", "local", "remote"]) == {
            "version": "plugin version version 1 here, 2 on srv",
            "sources": "plugin sources sources differ on srv",
            "local": "plugin local is not installed on srv",
            "remote": "plugin remote is not installed here",
        }
        unchecked = PluginCompatibility(self.MINE, self.THEIRS, "srv", False)
        assert unchecked.problem("sources") is None
        assert unchecked.problem("version") is not None

    def test_status(self):
        compatibility = PluginCompatibility(self.MINE, self.THEIRS, "srv", True)
        assert {name: compatibility.status(name, "local host")
                for name in ("same", "version", "sources", "local", "remote")} == {
            "same": "same",
            "version": "version differs, 1 on local host",
            "sources": "sources differ",
            "local": "not installed on srv",
            "remote": "not installed on local host",
        }
        unchecked = PluginCompatibility(self.MINE, self.THEIRS, "srv", False)
        assert unchecked.status("sources", "local host") == "sources differ, not checked"

    def test_json_round_trip(self):
        local = Identity.local()
        assert Identity.from_json(local.to_json()) == local
        assert set(local.sources) == {"gbs"} | set(local.plugins)
        bad = local.to_json()
        bad["sources"] = {"gbs": 1}
        with pytest.raises(WireError, match="gbs sources digest"):
            Identity.from_json(bad)

    def test_source_digest(self, tmp_path):
        tree = tmp_path / "pkg"
        (tree / "sub" / "__pycache__").mkdir(parents=True)
        (tree / "a.py").write_text("a = 1\n")
        (tree / "sub" / "b.py").write_text("b = 2\n")
        (tree / "sub" / "__pycache__" / "c.py").write_text("ignored\n")
        (tree / "data.txt").write_text("ignored\n")
        digest = SourceDigest.tree([tree])
        assert SourceDigest.tree([tree]) == digest

        moved = tmp_path / "moved"
        (moved / "sub").mkdir(parents=True)
        (moved / "a.py").write_text("a = 1\n")
        (moved / "sub" / "b.py").write_text("b = 2\n")
        assert SourceDigest.tree([moved]) == digest

        renamed = tmp_path / "renamed"
        renamed.mkdir()
        (renamed / "a.py").write_text("a = 1\n")
        (renamed / "b.py").write_text("b = 2\n")
        assert SourceDigest.tree([renamed]) != digest

        changed = tmp_path / "changed"
        (changed / "sub").mkdir(parents=True)
        (changed / "a.py").write_text("a = 1\n")
        (changed / "sub" / "b.py").write_text("b = 3\n")
        assert SourceDigest.tree([changed]) != digest

    def test_digest_from_file_map(self, tmp_path):
        (tmp_path / "a.py").write_text("a = 1\n")
        files = SourceDigest.files([tmp_path])
        assert files == {"a.py": hashlib.sha256(b"a = 1\n").hexdigest()}
        assert SourceDigest.tree([tmp_path]) == SourceDigest.digest(files)
        assert SourceDigest.digest({"b.py": files["a.py"]}) != SourceDigest.digest(files)

    def test_module_own_tree(self, tmp_path, monkeypatch):
        """A regular package extended by other installs only covers its
        own directory; a namespace package covers every part"""
        first, second = tmp_path / "first", tmp_path / "second"
        (first / "gbstest_regular" / "sub").mkdir(parents=True)
        (first / "gbstest_regular" / "__init__.py").write_text(
            "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n")
        (first / "gbstest_regular" / "sub" / "own.py").write_text("own = 1\n")
        (second / "gbstest_regular").mkdir(parents=True)
        (second / "gbstest_regular" / "other.py").write_text("other = 1\n")
        (first / "gbstest_namespace").mkdir()
        (first / "gbstest_namespace" / "a.py").write_text("a = 1\n")
        (second / "gbstest_namespace").mkdir()
        (second / "gbstest_namespace" / "b.py").write_text("b = 1\n")
        (first / "gbstest_plain.py").write_text("plain = 1\n")
        monkeypatch.syspath_prepend(str(second))
        monkeypatch.syspath_prepend(str(first))
        for name in ("gbstest_regular", "gbstest_namespace", "gbstest_plain"):
            monkeypatch.delitem(sys.modules, name, raising=False)
        try:
            regular = importlib.import_module("gbstest_regular")
            assert len(regular.__path__) == 2
            assert sorted(SourceDigest.module_files("gbstest_regular")) == ["__init__.py", "sub/own.py"]
            assert sorted(SourceDigest.module_files("gbstest_namespace")) == ["a.py", "b.py"]
            assert sorted(SourceDigest.module_files("gbstest_plain")) == ["gbstest_plain.py"]
        finally:
            for name in ("gbstest_regular", "gbstest_namespace", "gbstest_plain"):
                sys.modules.pop(name, None)

    def test_gbs_own_tree(self):
        files = SourceDigest.module_files("gbs")
        root = Path(gbs.__file__).resolve().parent
        assert files == SourceDigest.files([root])
        assert "remote/handshake.py" in files

    def test_protocol_checked_first(self):
        with pytest.raises(HandshakeError, match="Protocol version 999"):
            Identity.from_json({"protocol": 999, "anything": "else"})


class TestToolHost:
    def test_local(self, tmp_path):
        exe = tmp_path / "tool"
        exe.write_text("")
        config = GBSConfig(tools=[
            ToolConfig("yosys", None, "2026-03-24", {"executable": str(exe)}),
            ToolConfig("ghdl", "llvm", None, {"executable": str(tmp_path / "missing")}),
            ToolConfig("ghdl", "mcode", None, {}),
        ])
        host = LocalToolHost(config)
        assert host.tool_probe("yosys") is None
        assert host.tool_probe("yosys@2026-03-24") is None
        assert "not configured" in host.tool_probe("yosys@2020-01-01")
        assert "does not exist" in host.tool_probe("ghdl")
        assert host.tool_probe("ghdl:mcode") is None
        assert [t.identifier for t in host.tools()] == ["yosys@2026-03-24", "ghdl:llvm", "ghdl:mcode"]

    def test_remote_from_inventory(self):
        tools = [
            ToolDescription.from_json(t.to_json())
            for t in (ToolDescription("vivado", None, "2024.2"),
                      ToolDescription("quartus", "prime", None, "no licence"))
        ]
        host = RemoteToolHost("srv", tools, None, None)
        assert host.tool_probe("vivado") is None
        assert host.tool_probe("quartus:prime") == "no licence"
        assert "not configured on srv" in host.tool_probe("diamond")


class TestRemoteHostConfig:
    def test_parse_and_ssh_argv(self, tmp_path):
        path = tmp_path / "gbs.yaml"
        path.write_text(
            "remote_hosts:\n"
            "  buildsrv:\n"
            "    ssh: [user@buildsrv, -p, 2222]\n"
            "    command: [/opt/gbs env/bin/gbs, -v]\n"
            "  shellsrv:\n"
            "    ssh: shellsrv.example\n"
            "    command: PATH=~/.local/bin:$PATH gbs\n"
        )
        config = GBSConfig._parse_config_file(path)
        assert list(config.remote_hosts) == ["buildsrv", "shellsrv"]
        host = config.remote_hosts["buildsrv"]
        assert host.ssh == ["user@buildsrv", "-p", "2222"]

        name, argv = RemoteHost.ssh_argv("buildsrv", config)
        assert name == "buildsrv"
        assert argv == ["ssh", "-T", "user@buildsrv", "-p", "2222", "--",
                        "'/opt/gbs env/bin/gbs' -v remote serve --stdio"]
        name, argv = RemoteHost.ssh_argv("shellsrv", config)
        assert argv == ["ssh", "-T", "shellsrv.example", "--",
                        "PATH=~/.local/bin:$PATH gbs remote serve --stdio"]
        name, argv = RemoteHost.ssh_argv("other.example", config)
        assert name == "other.example"
        assert argv == ["ssh", "-T", "other.example", "--", "gbs remote serve --stdio"]
        assert RemoteHost.host_config("other.example", config).check_sources

    async def test_check_sources(self, tmp_path):
        path = tmp_path / "gbs.yaml"
        path.write_text(
            "remote_hosts:\n"
            "  loose: {ssh: a, check_sources: false}\n"
            "  strict: {ssh: b, check_sources: true}\n"
            "  default: {ssh: c}\n"
        )
        config = GBSConfig._parse_config_file(path)
        assert {n: h.check_sources for n, h in config.remote_hosts.items()} == {
            "loose": False, "strict": True, "default": True}
        assert not RemoteHost.host_config("loose", config).check_sources

        result = await CliRunner().invoke(config_cli, ["dump"], obj={"gbs_config": config})
        assert result.exit_code == 0, result.output
        lines = result.output.split("remote_hosts:\n")[1].splitlines()
        loose = next(i for i, line in enumerate(lines) if line.startswith("  loose:"))
        assert lines[loose + 1:loose + 4] == ["    ssh: ['a']", "    command: 'gbs'", "    check_sources: false"]
        assert sum(line.strip().startswith("check_sources") for line in lines) == 1

    def test_merge_overrides_by_name(self, tmp_path):
        base = tmp_path / "a.yaml"
        base.write_text("remote_hosts: {x: {ssh: [a]}, y: {ssh: [b]}}\n")
        top = tmp_path / "b.yaml"
        top.write_text("remote_hosts: {x: {ssh: [c]}}\n")
        merged = GBSConfig._merge_configs(
            GBSConfig._parse_config_file(base), GBSConfig._parse_config_file(top))
        assert {n: h.ssh for n, h in merged.remote_hosts.items()} == {"x": ["c"], "y": ["b"]}

    @pytest.mark.parametrize("entry, message", [
        ("x: {command: gbs}", "missing 'ssh'"),
        ("x: {ssh: a, cmd: gbs}", "unknown keys cmd"),
        ("x: {ssh: [], command: gbs}", "ssh must be"),
        ("x: {ssh: a, command: {a: b}}", "command must be"),
        ("x: {ssh: a, command: ' '}", "command is empty"),
        ("x: {ssh: a, check_sources: 'no'}", "check_sources must be a boolean"),
        ("x: {ssh: a, check_sources: 0}", "check_sources must be a boolean"),
        ("x: [a]", "must be a mapping"),
    ])
    def test_invalid_entry_is_an_error(self, tmp_path, entry, message):
        path = tmp_path / "gbs.yaml"
        path.write_text(f"remote_hosts: {{{entry}}}\n")
        with pytest.raises(ConfigError, match=message):
            GBSConfig._parse_config_file(path)
