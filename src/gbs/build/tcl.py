from __future__ import annotations
import asyncio
import os
from .. import logging
import re
from typing import AsyncIterator
from pathlib import Path

from .task import Task, BuildError
from .context import BuildContext
from .message import MessageSeverity, ToolMessage

__all__ = ["Session"]

class Expression:
    def __init__(self, expr):
        self.expr = expr

    def __str__(self):
        return self.expr

    @classmethod
    def from_str(cls, s: Any) -> Expression:
        if isinstance(s, Expression):
            return s
        return String(str(s))

class String(Expression):
    def __init__(self, value: str):
        self.value = str(value)

    def __str__(self):
        return f'{{{self.value}}}'

class BareWord(Expression):
    def __init__(self, value: str):
        self.value = value

    def __str__(self):
        return self.value

    @classmethod
    def from_str(cls, s: Any) -> Expression:
        if isinstance(s, Expression):
            return s
        return BareWord(str(s))

class Command(Expression):
    def __init__(self, argv):
        self.argv = [BareWord.from_str(argv[0])] + [Expression.from_str(x) for x in argv[1:]]

    def __str__(self):
        return " ".join([str(x) for x in self.argv])

class Expansion(Command):
    def __init__(self, argv):
        super().__init__(argv)

    def __str__(self):
        return f'[{super().__str__()}]'

class Session:
    """Shared TCL-interpreter interactive session with command serialization

    Manages a persistent subprocess that maintains state.
    """

    """
    This is the prompt from the interpreter. It is matched against
    stdout to know when interpreter is ready.
    """
    promt : str = "% "

    def __init__(self, argv: list[str], cwd: Path = Path("."), env: dict[str, str] | None = None):
        """Initialize TCL session

        Args:
            argv: Command and arguments to execute
            cwd: Working directory for the subprocess
            env: Additional environment variables to inject (merged with current environment)
        """
        self.argv = argv
        self.cwd = cwd
        self.env = env
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
                await self.__queue.put(None)
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

            # Merge additional environment variables with current environment
            process_env = None
            if self.env:
                process_env = os.environ.copy()
                process_env.update(self.env)
                self.__logger.debug(f"Injecting environment variables: {list(self.env.keys())}")

            self.__process = await asyncio.create_subprocess_exec(
                *self.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.cwd),
                env=process_env,
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
            if not m:
                break

    async def session_close(self):
        """
        Send commands to exit shell
        """
        await self.execute(Command(["exit"]))
    
    async def __cmd_send(self, cmd: Command) -> None:
        """Send TCL command to interpreter.

        This method should be called with lock held.
        """
        self.__logger.debug(f"send {cmd}")

        self.__process.stdin.write(f"{str(cmd)}\n".encode('utf-8'))
        await self.__process.stdin.drain()

    async def prepare(self):
        """
        Pre command step, could create a directory here
        """
        pass
        
    async def interact(self, cmd: Command) -> AsyncIterator[ToolMessage]:
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
                if msg is None:
                    break
                yield msg

    async def execute(self, cmd: Command) -> None:
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

        cmd = self.command()
        async for msg in self.session.interact(cmd):
            # Check for errors (skip non-ToolMessage objects like progress indicators)
            if hasattr(msg, 'severity'):
                had_error |= (msg.severity == MessageSeverity.ERROR)
            await self.message_handle(msg)
        if had_error:
            raise BuildError("Command generated error messages")

    async def message_handle(self, msg: ToolMessage) -> None:
        await self.add_message_obj(msg)
            
    def command(self) -> Command:
        """
        Factory function to override to get the command to run for
        this task
        """
        return None

class SimpleCommandTask(CommandTask):
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

    def command(self) -> Command:
        return self.__class__.command
