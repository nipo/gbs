"""Blob transfer between two gbs instances

A frame body is bounded, a blob is not: blobs travel in pieces, each
one request, so a transfer of any size works whatever the bound.
"""

from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Iterable

from .manifest import BlobStore
from .peer import Peer
from .wire import WireError, WireObject

__all__ = ["BlobTransfer"]


class BlobTransfer:
    """Sends blobs to, and fetches blobs from, a remote gbs

    Uses the `blob.have`, `blob.put` and `blob.get` methods of
    RemoteServer.

    Attributes:
        peer: Connection to the remote
        chunk: Size of the pieces blobs travel in
    """

    CHUNK = 16 << 20
    PARALLEL = 8

    def __init__(self, peer: Peer, chunk: int = CHUNK):
        if chunk <= 0:
            raise ValueError(f"Invalid chunk size {chunk}")
        self.peer = peer
        self.chunk = chunk

    async def missing(self, digests: Iterable[str]) -> list[str]:
        """Digests the remote store lacks"""
        digests = sorted(set(digests))
        if not digests:
            return []
        reply = await self.peer.request("blob.have", {"digests": digests})
        reader = WireObject(reply.result, "blob.have result")
        missing = reader.string_list("missing")
        reader.finish()
        unknown = set(missing) - set(digests)
        if unknown:
            raise WireError(f"blob.have answered with unrequested digest {sorted(unknown)[0]}")
        return missing

    async def upload(self, files: dict[str, Path]) -> int:
        """Send the blobs the remote lacks

        Args:
            files: Local file holding each blob, by digest

        Returns:
            Number of blobs sent
        """
        missing = await self.missing(files)
        limit = asyncio.Semaphore(self.PARALLEL)

        async def put(digest: str) -> None:
            async with limit:
                await self.put(digest, files[digest])

        await asyncio.gather(*(put(d) for d in missing))
        return len(missing)

    async def put(self, digest: str, path: Path) -> None:
        """Send one blob, read from a local file"""
        size = (await asyncio.to_thread(path.stat)).st_size
        offset = 0
        while True:
            data = await asyncio.to_thread(self.range_read, path, offset, min(self.chunk, size - offset))
            await self.peer.request(
                "blob.put", {"digest": digest, "offset": offset, "size": size}, body=data)
            offset += len(data)
            if offset >= size:
                return
            if not data:
                raise WireError(f"{path} shrank while it was sent")

    @staticmethod
    def range_read(path: Path, offset: int, size: int) -> bytes:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read(size)

    async def download(self, digests: Iterable[str], store: BlobStore) -> int:
        """Fetch into a local store the blobs it lacks

        Returns:
            Number of blobs fetched
        """
        missing = sorted(d for d in set(digests) if not store.has(d))
        limit = asyncio.Semaphore(self.PARALLEL)

        async def get(digest: str) -> None:
            async with limit:
                await self.get(digest, store)

        await asyncio.gather(*(get(d) for d in missing))
        return len(missing)

    async def get(self, digest: str, store: BlobStore) -> None:
        """Fetch one blob into a local store"""
        upload = await asyncio.to_thread(store.upload, digest)
        try:
            while True:
                reply = await self.peer.request(
                    "blob.get", {"digest": digest, "offset": upload.received, "size": self.chunk})
                reader = WireObject(reply.result, "blob.get result")
                size = reader.field("size", int)
                reader.finish()
                if upload.size is None:
                    upload.size = size
                elif upload.size != size:
                    raise WireError(f"Blob {digest} changed size while it was fetched")
                await asyncio.to_thread(upload.append, upload.received, reply.body)
                if upload.complete:
                    break
                if not reply.body:
                    raise WireError(f"Blob {digest} ends before its {size} bytes")
            await asyncio.to_thread(upload.finish)
        except BaseException:
            upload.abort()
            raise
