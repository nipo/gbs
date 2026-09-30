"""Framed byte channel between two gbs instances

A frame is a JSON object header followed by an optional binary body,
so file contents travel as they are rather than encoded in JSON:

    u32 BE header length | header (UTF-8 JSON object)
    u32 BE body length   | body

Both lengths are bounded; a frame exceeding a bound is refused on
either side before any of it is sent or buffered.
"""

from __future__ import annotations
import asyncio
import json
import struct
from dataclasses import dataclass
from typing import Any, Optional

__all__ = ["ChannelError", "FrameError", "ChannelClosed", "Frame", "FrameChannel"]


class ChannelError(Exception):
    """Base of channel failures"""
    pass


class FrameError(ChannelError):
    """A frame is malformed or exceeds a size bound"""
    pass


class ChannelClosed(ChannelError):
    """The channel is closed or the connection is lost"""
    pass


@dataclass(frozen=True)
class Frame:
    """One unit of transfer

    Attributes:
        header: JSON object
        body: Opaque bytes, empty when the frame carries none
    """
    header: dict[str, Any]
    body: bytes = b""


class FrameChannel:
    """Reads and writes frames over an asyncio stream pair

    Writes are serialized, so concurrent writers never interleave
    frames.
    """

    LENGTH = struct.Struct(">I")
    HEADER_MAX = 16 << 20
    BODY_MAX = 256 << 20

    def __init__(self,
                 reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter,
                 header_max: int = HEADER_MAX,
                 body_max: int = BODY_MAX):
        self.reader = reader
        self.writer = writer
        self.header_max = header_max
        self.body_max = body_max
        self.__write_lock = asyncio.Lock()

    def encode(self, frame: Frame) -> bytes:
        """Serialized form of a frame

        Raises:
            FrameError: If the header is not a JSON object or a part
                exceeds its bound.
        """
        if not isinstance(frame.header, dict):
            raise FrameError(f"Frame header must be an object, got {type(frame.header).__name__}")
        if not isinstance(frame.body, (bytes, bytearray, memoryview)):
            raise FrameError(f"Frame body must be bytes, got {type(frame.body).__name__}")
        try:
            header = json.dumps(frame.header, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as e:
            raise FrameError(f"Frame header is not plain JSON: {e}") from e
        self.size_check("header", len(header), self.header_max)
        self.size_check("body", len(frame.body), self.body_max)
        return b"".join((
            self.LENGTH.pack(len(header)), header,
            self.LENGTH.pack(len(frame.body)), bytes(frame.body),
        ))

    @staticmethod
    def size_check(part: str, size: int, bound: int) -> None:
        if size > bound:
            raise FrameError(f"Frame {part} of {size} bytes exceeds the {bound} bytes bound")

    async def write(self, frame: Frame) -> None:
        """Send a frame

        Raises:
            FrameError: If the frame cannot be encoded; nothing is sent.
            ChannelClosed: If the connection is lost.
        """
        data = self.encode(frame)
        async with self.__write_lock:
            if self.writer.is_closing():
                raise ChannelClosed("Channel is closed")
            try:
                self.writer.write(data)
                await self.writer.drain()
            except (ConnectionError, OSError) as e:
                raise ChannelClosed(f"Connection lost while writing: {e}") from e

    async def read(self) -> Optional[Frame]:
        """Receive a frame, None on end of stream between frames

        Raises:
            FrameError: If the stream ends within a frame, or the frame
                is malformed or exceeds a bound.
            ChannelClosed: If the connection is lost.
        """
        prefix = await self.__read_exactly(self.LENGTH.size, "header length", eof_ok=True)
        if prefix is None:
            return None
        (header_size,) = self.LENGTH.unpack(prefix)
        self.size_check("header", header_size, self.header_max)
        raw = await self.__read_exactly(header_size, "header")
        try:
            header = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise FrameError(f"Frame header is not valid JSON: {e}") from e
        if not isinstance(header, dict):
            raise FrameError(f"Frame header must be an object, got {type(header).__name__}")
        (body_size,) = self.LENGTH.unpack(await self.__read_exactly(self.LENGTH.size, "body length"))
        self.size_check("body", body_size, self.body_max)
        body = await self.__read_exactly(body_size, "body") if body_size else b""
        return Frame(header, body)

    async def __read_exactly(self, size: int, what: str, eof_ok: bool = False) -> Optional[bytes]:
        try:
            return await self.reader.readexactly(size)
        except asyncio.IncompleteReadError as e:
            if eof_ok and not e.partial:
                return None
            raise FrameError(
                f"Stream ends within frame {what} ({len(e.partial)} of {size} bytes)"
            ) from e
        except (ConnectionError, OSError) as e:
            raise ChannelClosed(f"Connection lost while reading: {e}") from e

    async def close(self, timeout: float = 10.0) -> None:
        """Close the writing side, after flushing what is pending

        Data the other side does not take within timeout is dropped.
        """
        if not self.writer.is_closing():
            self.writer.close()
        try:
            await asyncio.wait_for(asyncio.shield(self.writer.wait_closed()), timeout)
        except TimeoutError:
            self.writer.transport.abort()
        except (ConnectionError, OSError):
            pass
