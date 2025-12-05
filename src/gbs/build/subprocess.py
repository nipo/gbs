"""This module defines tools for handling subprocesses in the context
of gbs using asyncio.
"""

from __future__ import annotations
from pathlib import Path
import asyncio
from .message import *

__all__ = ["MessageSubprocess"]

class MessageSubprocess:
    """
    A subprocess that generates a stream of ToolMessage objects.

    Typical usage:

    proc = MessageSubprocess(["ls", "-1", "/tmp"])

    async for message in proc:
        # do something
        pass

    # proc.returncode has the exit status

    """
    def __init__(self,
                 argv: list[str],
                 cwd: Path = Path(".")):
        self.argv = argv
        self.cwd = cwd
        self.process = None
        self.__queue = asyncio.Queue()

    async def __aiter__(self) -> AsyncIterator[ToolMessage]:
        """Asynchronous iterator of messages. Messages from stdout and
        stderr transformers are mixed in the same iteration.  When
        iterator completes, process has exited.
        """
        await self.__launch()

        to_close = 2
        while to_close:
            msg = await self.__queue.get()
            if msg is None:
                to_close -= 1
                continue
            yield msg

        await self.process.wait()
        await self.stdout_task
        await self.stderr_task

    @property
    def returncode(self):
        if self.process:
            return self.process.returncode
        return None

    async def stdout_transform(self, lines: AsyncIterator[str]) -> AsyncIterator[ToolMessage]:
        """Transformer that receives lines of text from stdout one by
        one, may create a message from it.

        This may be overridden for custom message processing
        """
        async for line in lines:
            yield ToolMessage(severity = MessageSeverity.INFO,
                              message = line)

    async def stderr_transform(self, lines: AsyncIterator[str]) -> AsyncIterator[ToolMessage]:
        """Transformer that receives lines of text from stderr one by
        one, may create a message from it.

        This may be overridden for custom message processing
        """
        async for line in lines:
            yield ToolMessage(severity = MessageSeverity.ERROR,
                              message = line)

    async def __stream_liner(self, stream) -> AsyncIterator[str]:
        """Internal tool that takes a stream and yeilds it line by
        line
        """
        buffer = ""
        while True:
            chunk = await stream.read(1024)
            if not chunk:
                break

            buffer += chunk.decode('utf-8', errors='replace')

            while True:
                try:
                    line, buffer = buffer.split("\n", 1)
                except ValueError:
                    break
                yield line

    async def __stream_handler(self, stream, transformer):
        """Internal tool that takes a stream and puts it to queue
        """
        async for msg in transformer(self.__stream_liner(stream)):
            await self.__queue.put(msg)
        await self.__queue.put(None)

    async def __launch(self):
        if self.process:
            return

        self.process = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd),
        )

        self.stdout_task = asyncio.create_task(
            self.__stream_handler(self.process.stdout, self.stdout_transform))
        self.stderr_task = asyncio.create_task(
            self.__stream_handler(self.process.stderr, self.stderr_transform))

