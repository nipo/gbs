"""Serving side of a remote connection

`gbs remote serve --stdio` runs a RemoteServer on the process standard
input and output. Standard output carries frames only: the channel
gets its own copies of the standard descriptors, then descriptor 1 is
pointed to standard error and descriptor 0 to /dev/null, so neither
Python code nor tool subprocesses can corrupt or consume the channel.
"""

from __future__ import annotations
import asyncio
import copy
import itertools
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..config.model import GBSConfig
from ..logging import get_logger
from ..plugins import get_plugin_registry
from .channel import FrameChannel
from .handshake import HandshakeError, HelloReply, Identity
from .manifest import BlobStore, ContentManifest
from .peer import Call, MethodError, Peer, Reply
from .planning import PassContribution
from .segment import SegmentDescriptor
from .segment_run import SegmentRun
from .toolhost import LocalToolHost
from .wire import WireObject

__all__ = ["Workspace", "RemoteServer", "StdioChannel"]

logger = get_logger(__name__)


class Workspace:
    """Temporary directory remote work happens in

    Created on first use, removed by cleanup() unless kept.
    """

    def __init__(self, keep: bool = False, parent: Optional[Path] = None):
        self.keep = keep
        self.parent = parent
        self.__path: Optional[Path] = None

    @property
    def path(self) -> Path:
        if self.__path is None:
            self.__path = Path(tempfile.mkdtemp(prefix="gbs-remote-", dir=self.parent))
            logger.info(f"Workspace is {self.__path}")
        return self.__path

    @property
    def created(self) -> bool:
        return self.__path is not None

    def cleanup(self) -> None:
        if self.__path is None:
            return
        if self.keep:
            logger.warning(f"Keeping workspace {self.__path}")
            return
        shutil.rmtree(self.__path, onexc=self.__removal_failed)
        self.__path = None

    @staticmethod
    def __removal_failed(function, path, exc) -> None:
        logger.warning(f"Cannot remove {path} from workspace: {exc}")


class RemoteServer:
    """Serves requests of one client on a channel

    Methods:
        hello: Exchange identities, send the tool inventory. Required
            before any other method but shutdown; refused identities
            leave the session unusable.
        blob.have: {digests: [sha256...]} -> {missing: [sha256...]}
        blob.put: {digest, offset, size} with a piece of the content
            as body -> {}: pieces of a blob of size bytes come in
            order, the first at offset 0; the blob enters the store
            with its last piece.
        blob.get: {digest, offset, size} -> {size} with up to size
            bytes of the blob from offset as body; the result is the
            blob size.
        passes.contribute: {backend, config, requested_types,
            project_config} -> {passes: [PassContribution...]}: ask a
            backend for passes with the configuration of this host,
            and probe them here.
        segment.dispatch: SegmentDescriptor -> SegmentDispatchReply:
            materialize the inputs of the descriptor manifest, and
            dispatch the segment in a directory of the workspace.
        segment.execute: {id, manifest} -> {manifest}: bring the
            segment inputs to the manifest, build, and answer with the
            content of the outputs, whose blobs are then in the store.
            Tool messages and step progress come as `message` and
            `progress` events. A failed build answers a BuildFailed
            error whose data is {headline, report}. The segment
            directory is removed afterwards, unless the workspace is
            kept.
        shutdown: Answer, then close the connection.

    Planning queries run backend code, which is not safe to run out of
    the event loop thread (it reports through the feedback hub), so a
    long probe holds the connection up; clients query one at a time
    anyway.
    """

    def __init__(self, channel: FrameChannel, gbs_config: GBSConfig,
                 blob_store: BlobStore, workspace: Workspace):
        self.channel = channel
        self.gbs_config = gbs_config
        self.blob_store = blob_store
        self.workspace = workspace
        self.identity = Identity.local()
        self.client: Optional[Identity] = None
        self.uploads: dict[str, BlobStore.Upload] = {}
        self.segments: dict[int, SegmentRun] = {}
        self.__segment_ids = itertools.count(1)
        max_parallel = gbs_config.max_parallel if gbs_config.max_parallel is not None else 4
        self.semaphore = asyncio.Semaphore(max_parallel)
        self.peer = Peer(channel, "remote client")
        self.peer.method_register("hello", self.hello)
        self.peer.method_register("blob.have", self.blob_have)
        self.peer.method_register("blob.put", self.blob_put)
        self.peer.method_register("blob.get", self.blob_get)
        self.peer.method_register("passes.contribute", self.passes_contribute)
        self.peer.method_register("segment.dispatch", self.segment_dispatch)
        self.peer.method_register("segment.execute", self.segment_execute)
        self.peer.method_register("shutdown", self.shutdown)

    @staticmethod
    def blob_store_default() -> Path:
        cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(cache) if cache else Path.home() / ".cache"
        return base / "gbs" / "remote-blobs"

    async def run(self) -> None:
        """Serve until the client shuts the connection down or goes away"""
        self.peer.start()
        try:
            reason = await self.peer.wait_closed()
            logger.info(f"Connection ended: {reason}")
        finally:
            await self.peer.close()
            for upload in self.uploads.values():
                upload.abort()
            self.uploads.clear()
            self.workspace.cleanup()

    def session_check(self) -> None:
        if self.client is None:
            raise MethodError("HandshakeRequired", "hello must come first")
        mismatches = self.identity.mismatches(self.client)
        if mismatches:
            raise MethodError("Incompatible", "Client identity differs: " + ", ".join(
                f"{what} {mine} here, {theirs} on client" for what, mine, theirs in mismatches
            ))

    async def hello(self, call: Call) -> Any:
        if self.client is not None:
            raise MethodError("ProtocolError", "hello was already received")
        try:
            self.client = Identity.from_json(call.params)
        except HandshakeError as e:
            raise MethodError("Incompatible", str(e))
        for what, mine, theirs in self.identity.mismatches(self.client):
            logger.error(f"Client {what} is {theirs}, {mine} here")
        tools = LocalToolHost(self.gbs_config).tools()
        return HelloReply(self.identity, tools).to_json()

    async def blob_have(self, call: Call) -> Any:
        self.session_check()
        reader = WireObject(call.params, "blob.have params")
        digests = reader.string_list("digests")
        reader.finish()
        missing = [d for d in digests if not self.blob_store.has(d)]
        return {"missing": missing}

    async def blob_put(self, call: Call) -> Any:
        self.session_check()
        reader = WireObject(call.params, "blob.put params")
        digest = BlobStore.digest_check(reader.field("digest", str))
        offset = reader.field("offset", int)
        size = reader.field("size", int)
        reader.finish()

        upload = self.uploads.pop(digest, None)
        if offset == 0:
            if upload is not None:
                upload.abort()
            upload = await asyncio.to_thread(self.blob_store.upload, digest, size)
        elif upload is None or upload.size != size:
            raise MethodError("ProtocolError", f"Blob {digest}: no upload to continue at {offset}")
        try:
            await asyncio.to_thread(upload.append, offset, call.body)
            if upload.complete:
                await asyncio.to_thread(upload.finish)
            else:
                self.uploads[digest] = upload
        except BaseException:
            upload.abort()
            raise
        return {}

    async def blob_get(self, call: Call) -> Any:
        self.session_check()
        reader = WireObject(call.params, "blob.get params")
        digest = BlobStore.digest_check(reader.field("digest", str))
        offset = reader.field("offset", int)
        size = reader.field("size", int)
        reader.finish()
        if not self.blob_store.has(digest):
            raise MethodError("MissingBlob", f"No blob {digest}")
        total = (await asyncio.to_thread(self.blob_store.path(digest).stat)).st_size
        data = await asyncio.to_thread(
            self.blob_store.read, digest, offset, min(size, self.channel.body_max))
        return Reply({"size": total}, data)

    async def passes_contribute(self, call: Call) -> Any:
        self.session_check()
        reader = WireObject(call.params, "passes.contribute params")
        name = reader.field("backend", str)
        config = reader.field("config", dict)
        requested_types = set(reader.string_list("requested_types"))
        project_config = reader.field("project_config", dict)
        reader.finish()

        backends = [b for b in get_plugin_registry().get_all_backends() if b.name == name]
        if len(backends) != 1:
            raise MethodError("UnknownBackend", f"{len(backends)} backend(s) named {name!r} here")
        backend, = backends
        contributed = await LocalToolHost(self.gbs_config).passes_contribute(
            backend, copy.deepcopy(config), requested_types, project_config)
        return PassContribution.list_to_json([
            PassContribution.from_pass(pass_obj, problem, name, config, requested_types)
            for pass_obj, problem in contributed
        ])

    async def segment_dispatch(self, call: Call) -> Any:
        self.session_check()
        descriptor = SegmentDescriptor.from_json(call.params)
        if descriptor.manifest is not None:
            self.blobs_check(descriptor.manifest)
        id = next(self.__segment_ids)
        run = SegmentRun(
            id, self.workspace.path / f"segment-{id}", descriptor,
            self.gbs_config, self.blob_store, self.semaphore, self.workspace.keep)
        reply = await run.dispatch()
        self.segments[id] = run
        return reply.to_json()

    async def segment_execute(self, call: Call) -> Any:
        self.session_check()
        reader = WireObject(call.params, "segment.execute params")
        id = reader.field("id", int)
        manifest = ContentManifest.from_json(reader.field("manifest", dict))
        reader.finish()
        run = self.segments.get(id)
        if run is None:
            raise MethodError("UnknownSegment", f"No segment {id}")
        self.blobs_check(manifest)
        outputs = await run.execute(call, manifest)
        return {"manifest": outputs.to_json()}

    def blobs_check(self, manifest: ContentManifest) -> None:
        missing = sorted(d for d in manifest.digests() if not self.blob_store.has(d))
        if missing:
            raise MethodError(
                "MissingBlob", f"{len(missing)} blob(s) of the manifest are missing, first {missing[0]}")

    async def shutdown(self, call: Call) -> Any:
        call.closing()
        return {}


class StdioChannel:
    """Frame channel over the process standard input and output"""

    @staticmethod
    def descriptors_take() -> tuple[int, int]:
        """Take standard input and output away from everything else

        Returns:
            Descriptors of the channel input and output
        """
        sys.stdout.flush()
        channel_in = os.dup(0)
        channel_out = os.dup(1)
        os.dup2(2, 1)
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.close(null)
        return channel_in, channel_out

    @classmethod
    async def open(cls) -> FrameChannel:
        channel_in, channel_out = cls.descriptors_take()
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader),
            os.fdopen(channel_in, "rb", buffering=0),
        )
        transport, protocol = await loop.connect_write_pipe(
            lambda: asyncio.StreamReaderProtocol(asyncio.StreamReader()),
            os.fdopen(channel_out, "wb", buffering=0),
        )
        writer = asyncio.StreamWriter(transport, protocol, None, loop)
        return FrameChannel(reader, writer)
