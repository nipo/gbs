"""Tests for the remote framing, messaging and tool hosts"""

import asyncio
import socket
import struct

import pytest

from gbs.config.model import ConfigError, GBSConfig, ToolConfig
from gbs.remote import (
    ChannelClosed, Frame, FrameChannel, FrameError, Identity, LocalToolHost,
    MethodError, Peer, RemoteError, RemoteHost, RemoteToolHost, Reply,
    SourceDigest, ToolDescription, HandshakeError, WireError, WireFormat,
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
    def test_mismatch_lists_everything(self):
        mine = Identity(WireFormat.VERSION, "1.0", {"p": "1", "q": "2"},
                        {"gbs": "aa", "p": "11", "q": "22"})
        theirs = Identity(WireFormat.VERSION, "1.1", {"p": "1", "q": "3", "r": "1"},
                          {"gbs": "ab", "p": "11", "q": "23", "r": "33"})
        with pytest.raises(HandshakeError) as e:
            mine.check(theirs, "local host", "srv")
        text = str(e.value)
        assert "gbs version: 1.0 on local host, 1.1 on srv" in text
        assert "plugin q: 2 on local host, 3 on srv" in text
        assert "plugin r: missing on local host, 1 on srv" in text
        assert "gbs sources: aa on local host, ab on srv" in text
        assert "plugin q sources: 22 on local host, 23 on srv" in text
        assert "plugin r sources: missing on local host, 33 on srv" in text
        assert "plugin p sources" not in text
        mine.check(Identity(WireFormat.VERSION, "1.0", {"p": "1", "q": "2"},
                            {"gbs": "aa", "p": "11", "q": "22"}), "a", "b")

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
        host = RemoteToolHost("srv", tools, None)
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
        ("x: [a]", "must be a mapping"),
    ])
    def test_invalid_entry_is_an_error(self, tmp_path, entry, message):
        path = tmp_path / "gbs.yaml"
        path.write_text(f"remote_hosts: {{{entry}}}\n")
        with pytest.raises(ConfigError, match=message):
            GBSConfig._parse_config_file(path)
