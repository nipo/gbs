"""Serving side of a remote connection

`gbs remote serve --stdio` runs a RemoteServer on the process standard
input and output. Standard output carries frames only: the channel
gets its own copies of the standard descriptors, then descriptor 1 is
pointed to standard error and descriptor 0 to /dev/null, so neither
Python code nor tool subprocesses can corrupt or consume the channel.
"""

from __future__ import annotations
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..config.model import GBSConfig
from ..logging import get_logger
from .channel import FrameChannel
from .handshake import HandshakeError, HelloReply, Identity
from .manifest import BlobStore
from .peer import Call, MethodError, Peer
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
        blob.put: {digest} with the content as body -> {}
        shutdown: Answer, then close the connection.
    """

    def __init__(self, channel: FrameChannel, gbs_config: GBSConfig,
                 blob_store: BlobStore, workspace: Workspace):
        self.gbs_config = gbs_config
        self.blob_store = blob_store
        self.workspace = workspace
        self.identity = Identity.local()
        self.client: Optional[Identity] = None
        self.peer = Peer(channel, "remote client")
        self.peer.method_register("hello", self.hello)
        self.peer.method_register("blob.have", self.blob_have)
        self.peer.method_register("blob.put", self.blob_put)
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
        reader.finish()
        await asyncio.to_thread(self.blob_store.bytes_add, call.body, digest)
        return {}

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
