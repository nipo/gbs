from __future__ import annotations
import asyncio
from .. import logging
import re
from typing import AsyncIterator
from pathlib import Path

import shlex
from .task import Task, BuildError
from .context import BuildContext
from .message import MessageSeverity, ToolMessage

__all__ = ["Session"]

_eoc_marker = object()

class Session:
    """Shared shell-like interpreter interactive session with command serialization

    Manages a persistent subprocess that maintains state.
    """

    """
    This is the prompt from the interpreter. It is matched against
    stdout to know when interpreter is ready.
    """
    promt : str = "$ "

    def __init__(self, argv: list[str], cwd: Path = Path(".")):
        self.argv = argv
        self.cwd = cwd
        self.__process = None
        self.__queue = asyncio.Queue()
        self.__logger = logging.get_logger(self.__class__.__name__)
        self.__lock = asyncio.Lock()

    @property
    def returncode(self):
        if self.__process:
            return self.__process.returncode
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
            if buffer == self.prompt:
                # Marker to say command is over
                await self.__queue.put(_eoc_marker)
                buffer = ""

    async def __stream_handler(self, stream, transformer):
        """Internal tool that takes a stream and puts it to queue
        """
        async for msg in transformer(self.__stream_liner(stream)):
            await self.__queue.put(msg)
        await self.__queue.put(None)

    async def __launch(self):
        """
        Launch the process
        """
        async with self.__lock:
            if self.__process:
                return
            self.__logger.debug(f"Launching interpreter with argv={self.argv}")

            await self.prepare()

            self.cwd.mkdir(parents = True, exist_ok = True)

            self.__process = await asyncio.create_subprocess_exec(
                *self.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.cwd),
            )

            self.stdout_task = asyncio.create_task(
                self.__stream_handler(self.__process.stdout, self.stdout_transform))
            self.stderr_task = asyncio.create_task(
                self.__stream_handler(self.__process.stderr, self.stderr_transform))

        await self.session_init()

    async def session_init(self):
        """
        Send mandatory commands at shell init
        """
        while True:
            m = await self.__queue.get()
            if m is _eoc_marker:
                break

    async def session_close(self):
        """
        Send commands to exit shell
        """
        await self.execute(["exit"])
    
    async def __cmd_send(self, cmd: list[str]) -> None:
        """Send TCL command to interpreter.

        This method should be called with lock held.
        """
        self.__logger.debug(f"send {cmd}")

        rcmd = shlex.join(cmd)
        self.__process.stdin.write((rcmd + "\n").encode('utf-8'))
        await self.__process.stdin.drain()

    async def prepare(self):
        """
        Pre command step, could create a directory here
        """
        pass
        
    async def interact(self, cmd: list[str]) -> AsyncIterator[ToolMessage]:
        """Send TCL command and yield ToolMessage objects as they
        arrive, as parsed by stream processors

        Args:
            cmd: TCL command to execute

        Yields:
            ToolMessage
        """
        await self.__launch()
        async with self.__lock:
            await self.__cmd_send(cmd)
            while True:
                msg = await self.__queue.get()
                if msg is _eoc_marker:
                    break
                yield msg

    async def execute(self, cmd: list[str]) -> None:
        """
        Helper that runs a command and wait for completion
        """
        async for msg in self.interact(cmd):
            pass

    async def close(self):
        """Shutdown shell cleanly"""
        if self.__process is None:
            return
        self.session_close()
        async with self.__lock:
            try:
                await asyncio.wait_for(self.__process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.__process.kill()
                await self.__process.wait()
            finally:
                self.__process = None

class CommandTask(Task):
    def __init__(
            self,
            dispatcher: "Dispatcher",
            name: str,
            session: Session,
            inputs: list,
            outputs: list,
            description: str = "",
    ):
        super().__init__(dispatcher = dispatcher,
                         name = name,
                         inputs = inputs,
                         outputs = outputs,
                         description = description)
        self.session = session

    async def prepare(self):
        """
        Pre command step, could create a directory here
        """
        pass

    async def work(self):
        """
        Default implementation for interacting with the interpreter
        """
        had_error = False

        await self.prepare()
        
        cmd = self.command_get()
        async for msg in self.session.interact(cmd):
            had_error |= (msg.severity == MessageSeverity.ERROR)
            await self.message_handle(msg)
        if had_error:
            raise BuildError("Command generated error messages")

    async def message_handle(self, msg: ToolMessage) -> None:
        await self.add_message_obj(msg)
            
    def command_get(self) -> list[str]:
        """
        Factory function to override to get the command to run for
        this task
        """
        return None

class SimpleCommandTask(CommandTask):
    name: str
    description: str
    command: list[str]

    def __init__(self,
                 dispatcher: "Dispatcher",
                 session: Session,
                 inputs = [],
                 outputs = []):
        super().__init__(
            dispatcher=dispatcher,
            name=self.__class__.name,
            session = session,
            inputs=inputs,
            outputs=outputs,
            description=self.__class__.description,
        )

    def command_get(self) -> list[str]:
        return self.__class__.command
