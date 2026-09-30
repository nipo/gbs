"""End-to-end tests of `gbs remote serve --stdio` in a child process"""

import asyncio
import hashlib
import os
import sys
from pathlib import Path

import pytest

import gbs
from gbs.remote import (
    BlobStore, BlobTransfer, Frame, FrameChannel, Identity, Peer, RemoteError, RemoteHost,
    RemoteHostError, WireFormat, Workspace,
)

SRC = Path(gbs.__file__).resolve().parent.parent


@pytest.fixture
def remote(tmp_path):
    """Isolated home, cache and working directory for a server"""
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config" / "gbs.yaml").write_text(
        "tools:\n"
        f"  - name: yosys\n"
        f"    version: '2026-03-24'\n"
        f"    config: {{executable: {sys.executable}}}\n"
        f"  - name: vivado\n"
        f"    config: {{executable: {tmp_path / 'nowhere' / 'vivado'}}}\n"
    )
    work = tmp_path / "work"
    work.mkdir()
    cache = tmp_path / "cache"
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "XDG_CACHE_HOME": str(cache),
        # A different HOME hides user site-packages: pass the whole path
        "PYTHONPATH": os.pathsep.join([str(SRC)] + [p for p in sys.path if p]),
    })

    class Remote:
        argv = [sys.executable, "-m", "gbs", "remote", "serve", "--stdio"]
        blobs = cache / "gbs" / "remote-blobs"

        @staticmethod
        async def connect(argv=None, identity=None):
            return await RemoteHost.connect(
                "test", argv or Remote.argv, env=env, cwd=str(work), identity=identity)

        @staticmethod
        async def spawn(argv=None):
            return await asyncio.create_subprocess_exec(
                *(argv or Remote.argv), env=env, cwd=str(work),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

    Remote.env = env
    Remote.tmp = tmp_path
    Remote.work = work
    return Remote


async def test_handshake(remote):
    async with await remote.connect() as host:
        assert host.hello.identity == Identity.local()
        tools = {t.identifier: t for t in host.hello.tools}
        assert tools["yosys@2026-03-24"].problem is None
        assert "does not exist" in tools["vivado"].problem
        assert host.tool_host.tool_probe("yosys") is None
        assert host.tool_host.tool_probe("vivado") is not None
        assert "not configured on test" in host.tool_host.tool_probe("diamond")
    assert host.process.returncode == 0
    assert host.peer.closed is not None


async def test_mismatch_refused(remote):
    local = Identity.local()
    plugins = dict(local.plugins)
    plugins["gbs.builtin.ghdl"] = "0.0.1"
    fake = Identity(local.protocol, "0.0.0-fake", plugins, local.sources)
    with pytest.raises(RemoteHostError) as e:
        await remote.connect(identity=fake)
    text = str(e.value)
    assert f"gbs version: 0.0.0-fake on local host, {gbs.__version__} on test" in text
    assert f"plugin gbs.builtin.ghdl: 0.0.1 on local host, {local.plugins['gbs.builtin.ghdl']} on test" in text


async def test_source_mismatch_refused(remote):
    local = Identity.local()
    sources = dict(local.sources)
    sources["gbs"] = "0" * 64
    sources["gbs.builtin.yosys"] = "1" * 64
    fake = Identity(local.protocol, local.gbs, local.plugins, sources)
    with pytest.raises(RemoteHostError) as e:
        await remote.connect(identity=fake)
    text = str(e.value)
    assert f"gbs sources: {'0' * 64} on local host, {local.sources['gbs']} on test" in text
    assert "plugin gbs.builtin.yosys sources: " in text
    assert "gbs version" not in text


async def test_serve_leaves_cwd_untouched(remote):
    async with await remote.connect() as host:
        await host.peer.request("blob.have", {"digests": []})
    assert list(remote.work.iterdir()) == []


async def test_server_refuses_before_and_after_bad_hello(remote):
    process = await remote.spawn()
    peer = Peer(FrameChannel(process.stdout, process.stdin), "raw")
    async with peer:
        with pytest.raises(RemoteError) as e:
            await peer.request("blob.have", {"digests": []})
        assert e.value.type == "HandshakeRequired"
        fake = Identity(WireFormat.VERSION, "0.0.0-fake", {}, {})
        await peer.request("hello", fake.to_json())
        with pytest.raises(RemoteError) as e:
            await peer.request("blob.have", {"digests": []})
        assert e.value.type == "Incompatible"
        with pytest.raises(RemoteError) as e:
            await peer.request("hello", fake.to_json())
        assert e.value.type == "ProtocolError"
        with pytest.raises(RemoteError) as e:
            await peer.request("hello", {"protocol": WireFormat.VERSION + 1})
        assert e.value.type == "ProtocolError"
    assert await asyncio.wait_for(process.wait(), 30) == 0


async def test_blob_have_put(remote):
    contents = [b"first blob", b"", b"\x00\xff" * 1000]
    digests = [hashlib.sha256(c).hexdigest() for c in contents]
    async with await remote.connect() as host:
        reply = await host.peer.request("blob.have", {"digests": digests})
        assert reply.result == {"missing": digests}
        for digest, content in zip(digests[:2], contents):
            await host.peer.request("blob.put", {"digest": digest, "offset": 0, "size": len(content)},
                                    body=content)
        reply = await host.peer.request("blob.have", {"digests": digests})
        assert reply.result == {"missing": digests[2:]}

        with pytest.raises(RemoteError) as e:
            await host.peer.request("blob.put", {"digest": digests[2], "offset": 0, "size": 5},
                                    body=b"other")
        assert e.value.type == "WireError"
        with pytest.raises(RemoteError) as e:
            await host.peer.request("blob.have", {"digests": ["nope"]})
        assert e.value.type == "WireError"
        with pytest.raises(RemoteError) as e:
            await host.peer.request("blob.have", {"digests": [], "extra": 1})
        assert e.value.type == "WireError"

    store = BlobStore(remote.blobs)
    for digest, content in zip(digests[:2], contents):
        assert store.path(digest).read_bytes() == content
    assert not store.has(digests[2])


async def test_chunked_transfer_both_ways(remote, tmp_path):
    content = os.urandom(300_000)
    source = tmp_path / "big.bin"
    source.write_bytes(content)
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    digests = [hashlib.sha256(c).hexdigest() for c in (content, b"")]
    local = BlobStore(tmp_path / "local-store")

    async with await remote.connect() as host:
        methods = []
        request = host.peer.request

        async def counting(method, *args, **kwargs):
            methods.append(method)
            return await request(method, *args, **kwargs)

        host.peer.request = counting
        up = BlobTransfer(host.peer, chunk=64 << 10)
        assert await up.upload(dict(zip(digests, (source, empty)))) == 2
        assert methods.count("blob.put") == 5 + 1
        assert await up.upload({digests[0]: source}) == 0

        down = BlobTransfer(host.peer, chunk=100_000)
        assert await down.download(digests, local) == 2
        assert methods.count("blob.get") == 3 + 1
        assert await down.download(digests, local) == 0

    assert local.path(digests[0]).read_bytes() == content
    assert local.path(digests[1]).read_bytes() == b""
    assert BlobStore(remote.blobs).path(digests[0]).read_bytes() == content


async def test_chunked_put_is_checked(remote):
    content = b"0123456789"
    digest = hashlib.sha256(content).hexdigest()
    async with await remote.connect() as host:
        put = host.peer.request
        with pytest.raises(RemoteError) as e:
            await put("blob.put", {"digest": digest, "offset": 4, "size": 10}, body=content[4:])
        assert e.value.type == "ProtocolError"
        await put("blob.put", {"digest": digest, "offset": 0, "size": 10}, body=content[:4])
        with pytest.raises(RemoteError) as e:
            await put("blob.put", {"digest": digest, "offset": 5, "size": 10}, body=content[5:])
        assert e.value.type == "WireError"
        await put("blob.put", {"digest": digest, "offset": 0, "size": 10}, body=content[:4])
        with pytest.raises(RemoteError) as e:
            await put("blob.put", {"digest": digest, "offset": 4, "size": 10}, body=b"xxxxxx")
        assert e.value.type == "WireError"
        reply = await host.peer.request("blob.have", {"digests": [digest]})
        assert reply.result == {"missing": [digest]}

        await put("blob.put", {"digest": digest, "offset": 0, "size": 10}, body=content[:4])
        await put("blob.put", {"digest": digest, "offset": 4, "size": 10}, body=content[4:])
        reply = await host.peer.request("blob.get", {"digest": digest, "offset": 8, "size": 100})
        assert reply.result == {"size": 10}
        assert reply.body == b"89"
        with pytest.raises(RemoteError) as e:
            await host.peer.request("blob.get", {"digest": "0" * 64, "offset": 0, "size": 1})
        assert e.value.type == "MissingBlob"
    assert not [p for p in remote.blobs.iterdir() if p.name.startswith(".tmp-")]


async def test_blob_store_option(remote):
    store_dir = remote.tmp / "elsewhere"
    argv = remote.argv + ["--blob-store", str(store_dir)]
    digest = hashlib.sha256(b"x").hexdigest()
    async with await remote.connect(argv) as host:
        await host.peer.request("blob.put", {"digest": digest, "offset": 0, "size": 1}, body=b"x")
    assert BlobStore(store_dir).has(digest)
    assert not remote.blobs.exists()


async def test_start_failure_reports_remote_output(remote):
    argv = [sys.executable, "-c", "import sys; sys.stderr.write('boom: no gbs here\\n'); sys.exit(3)"]
    with pytest.raises(RemoteHostError) as e:
        await remote.connect(argv)
    assert "handshake failed" in str(e.value)
    assert "boom: no gbs here" in str(e.value)


async def test_client_loss_ends_server(remote):
    process = await remote.spawn()
    process.stdin.close()
    assert await asyncio.wait_for(process.wait(), 30) == 0
    assert await process.stdout.read() == b""


async def test_stdout_carries_frames_only(remote, tmp_path):
    """Writes to standard output after startup, from Python or from the
    descriptor itself, land on standard error."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        "import atexit, os, sys\n"
        "atexit.register(lambda: (print('printed-at-exit'), sys.stdout.flush(),\n"
        "                         os.write(1, b'written-to-fd-1\\n')))\n"
    )
    remote.env["PYTHONPATH"] = os.pathsep.join([str(site), remote.env["PYTHONPATH"]])
    argv = [sys.executable, "-m", "gbs", "-d", "remote", "serve", "--stdio"]
    process = await remote.spawn(argv)

    channel = FrameChannel(process.stdout, process.stdin)
    digest = hashlib.sha256(b"blob").hexdigest()
    requests = [
        ("hello", Identity.local().to_json(), b""),
        ("blob.put", {"digest": digest, "offset": 0, "size": 4}, b"blob"),
        ("nothing", None, b""),
    ]
    for id, (method, params, body) in enumerate(requests, 1):
        await channel.write(Frame(
            {"kind": "request", "id": id, "method": method, "params": params}, body))
    frames = [await channel.read() for _ in requests]
    await channel.write(Frame({"kind": "request", "id": 4, "method": "shutdown", "params": None}))
    frames.append(await channel.read())
    assert await channel.read() is None
    stderr = await process.stderr.read()
    assert await asyncio.wait_for(process.wait(), 30) == 0

    assert sorted(f.header["id"] for f in frames) == [1, 2, 3, 4]
    assert all(f.header["kind"] == "response" for f in frames)
    assert b"printed-at-exit" in stderr
    assert b"written-to-fd-1" in stderr


class TestWorkspace:
    def test_lazy_and_wiped(self, tmp_path):
        workspace = Workspace(parent=tmp_path)
        assert not workspace.created
        path = workspace.path
        assert path.parent == tmp_path and path.is_dir()
        (path / "d").mkdir()
        (path / "d" / "f").write_text("x")
        workspace.cleanup()
        assert not path.exists()
        assert not workspace.created

    def test_kept(self, tmp_path):
        workspace = Workspace(keep=True, parent=tmp_path)
        path = workspace.path
        workspace.cleanup()
        assert path.is_dir()
