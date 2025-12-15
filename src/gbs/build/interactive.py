"""Base interactive session for shell-like interpreters

Provides a common base class for managing persistent subprocess sessions
that maintain state between commands (TCL interpreters, shells, etc.).
"""

from __future__ import annotations
import asyncio
import os
import pty
from abc import ABC, abstractmethod
from typing import AsyncIterator, Generic, TypeVar
from pathlib import Path

from .. import logging
from .task import Task, BuildError
from ..ui.messages import MessageSeverity, ToolMessage

__all__ = ["Session", "CommandTask"]

# Sentinel object for end-of-command marker
_eoc_marker = object()

# Type variable for command type
CommandT = TypeVar('CommandT')


class Session(ABC, Generic[CommandT]):
    """Base interactive session with command serialization

    Manages a persistent subprocess that maintains state between commands.
    Subclasses must implement command serialization for their specific
    command format.

    Type parameter CommandT represents the command type (e.g., tcl.Command
    or list[str] for shell commands).
    """

    prompt: str = "% "
    """Prompt string matched against stdout to detect command completion"""

    def __init__(
        self,
        argv: list[str],
        cwd: Path = Path("."),
        env: dict[str, str] | None = None,
        use_pty: bool = False,
    ):
        """Initialize interactive session

        Args:
            argv: Command and arguments to execute
            cwd: Working directory for the subprocess
            env: Additional environment variables to inject (merged with current environment)
            use_pty: Use pseudo-tty instead of pipes for stdin/stdout. Some tools
                     (like Vivado) require a tty for proper interactive behavior.
        """
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.use_pty = use_pty
        self._process = None
        self._queue = asyncio.Queue()
        self._logger = logging.get_logger(self.__class__.__name__)
        self._lock = asyncio.Lock()

        # PTY-specific state
        self._pty_master_fd: int | None = None
        self._pty_master_file = None
        self._pty_transport = None

        # Abstracted I/O (set in _launch)
        self._stdout_stream = None
        self._stdin_writer = None

    @property
    def returncode(self):
        if self._process:
            return self._process.returncode
        return None

    @abstractmethod
    def _cmd_serialize(self, cmd: CommandT) -> str:
        """Serialize command to string for sending to interpreter

        Args:
            cmd: Command to serialize

        Returns:
            String representation to send to interpreter
        """
        ...

    @abstractmethod
    def _exit_command(self) -> CommandT:
        """Return the command to exit the interpreter

        Returns:
            Exit command in the appropriate format
        """
        ...

    async def stdout_transform(self, lines: AsyncIterator[str]) -> AsyncIterator[ToolMessage]:
        """Transform stdout lines into ToolMessage objects

        Override for custom message processing.

        Args:
            lines: Async iterator of stdout lines

        Yields:
            ToolMessage objects
        """
        async for line in lines:
            yield ToolMessage(severity=MessageSeverity.INFO, message=line)

    async def stderr_transform(self, lines: AsyncIterator[str]) -> AsyncIterator[ToolMessage]:
        """Transform stderr lines into ToolMessage objects

        Override for custom message processing.

        Args:
            lines: Async iterator of stderr lines

        Yields:
            ToolMessage objects
        """
        async for line in lines:
            yield ToolMessage(severity=MessageSeverity.ERROR, message=line)

    async def _stream_liner(self, stream) -> AsyncIterator[str]:
        """Read stream and yield lines, detecting prompt for command completion"""
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
                # Strip trailing \r (carriage return) from PTY output
                # PTYs often use \r\n line endings
                yield line.rstrip('\r')

            if buffer == self.prompt:
                # Marker to say command is over
                await self._queue.put(_eoc_marker)
                buffer = ""

    async def _stream_handler(self, stream, transformer):
        """Read stream, transform lines, and put messages to queue"""
        async for msg in transformer(self._stream_liner(stream)):
            await self._queue.put(msg)
        await self._queue.put(None)

    async def _launch_with_pipes(self, process_env):
        """Launch process with pipe-based I/O"""
        self._process = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd),
            env=process_env,
        )

        self._stdout_stream = self._process.stdout
        self._stdin_writer = self._process.stdin

        self.stdout_task = asyncio.create_task(
            self._stream_handler(self._process.stdout, self.stdout_transform))
        self.stderr_task = asyncio.create_task(
            self._stream_handler(self._process.stderr, self.stderr_transform))

    async def _launch_with_pty(self, process_env):
        """Launch process with pseudo-tty for stdin/stdout"""
        # Create pty pair
        master_fd, slave_fd = pty.openpty()
        self._pty_master_fd = master_fd

        try:
            # Launch process with slave as stdin/stdout, keep stderr as pipe
            self._process = await asyncio.create_subprocess_exec(
                *self.argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.cwd),
                env=process_env,
            )
        finally:
            # Close slave fd in parent - child has its own copy
            os.close(slave_fd)

        # Wrap master fd in async stream reader
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)

        # Open master fd as file object for read pipe
        self._pty_master_file = os.fdopen(master_fd, 'rb', buffering=0)
        self._pty_transport, _ = await loop.connect_read_pipe(
            lambda: protocol, self._pty_master_file
        )

        self._stdout_stream = reader

        # Create a simple writer wrapper for the pty master fd
        class PtyWriter:
            def __init__(self, fd):
                self._fd = fd

            def write(self, data: bytes):
                os.write(self._fd, data)

            async def drain(self):
                # PTY writes are synchronous, no drain needed
                pass

        self._stdin_writer = PtyWriter(master_fd)

        # Start stream handlers
        self.stdout_task = asyncio.create_task(
            self._stream_handler(self._stdout_stream, self.stdout_transform))
        self.stderr_task = asyncio.create_task(
            self._stream_handler(self._process.stderr, self.stderr_transform))

    async def _launch(self):
        """Launch the interpreter process"""
        async with self._lock:
            if self._process:
                return
            self._logger.debug(f"Launching interpreter with argv={self.argv} (pty={self.use_pty})")

            await self.prepare()

            self.cwd.mkdir(parents=True, exist_ok=True)

            # Merge additional environment variables with current environment
            process_env = None
            if self.env:
                process_env = os.environ.copy()
                process_env.update(self.env)
                self._logger.debug(f"Injecting environment variables: {list(self.env.keys())}")

            if self.use_pty:
                await self._launch_with_pty(process_env)
            else:
                await self._launch_with_pipes(process_env)

        await self.session_init()

    async def session_init(self):
        """Initialize session after process launch

        Waits for initial prompt. Override to send initialization commands.
        """
        while True:
            m = await self._queue.get()
            if m is _eoc_marker:
                break

    async def session_close(self):
        """Send commands to exit the interpreter"""
        await self.execute(self._exit_command())

    async def _cmd_send(self, cmd: CommandT) -> None:
        """Send command to interpreter

        This method should be called with lock held.
        """
        cmd_str = self._cmd_serialize(cmd)
        self._logger.debug(f"send {cmd_str}")

        self._stdin_writer.write(f"{cmd_str}\n".encode('utf-8'))
        await self._stdin_writer.drain()

    async def prepare(self):
        """Pre-launch preparation hook

        Override to perform setup before process launch.
        """
        pass

    async def interact(self, cmd: CommandT) -> AsyncIterator[ToolMessage]:
        """Send command and yield messages as they arrive

        Args:
            cmd: Command to execute

        Yields:
            ToolMessage objects from stdout/stderr processing
        """
        await self._launch()
        async with self._lock:
            await self._cmd_send(cmd)
            while True:
                msg = await self._queue.get()
                if msg is _eoc_marker:
                    break
                yield msg

    async def execute(self, cmd: CommandT) -> None:
        """Execute command and wait for completion

        Args:
            cmd: Command to execute
        """
        async for msg in self.interact(cmd):
            pass

    async def close(self):
        """Shutdown interpreter cleanly"""
        if self._process is None:
            return
        await self.session_close()
        async with self._lock:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            finally:
                self._process = None

                # Clean up PTY resources
                if self._pty_transport:
                    self._pty_transport.close()
                    self._pty_transport = None
                # Note: closing transport closes the underlying file,
                # which closes the fd, so we don't close _pty_master_fd separately
                self._pty_master_file = None
                self._pty_master_fd = None


class CommandTask(Task, Generic[CommandT]):
    """Base task class for interactive session commands

    Provides common infrastructure for tasks that execute commands
    in an interactive session.
    """

    def __init__(
            self,
            dispatcher: "Dispatcher",
            name: str,
            session: Session[CommandT],
            inputs: list,
            outputs: list,
            description: str = "",
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=name,
            inputs=inputs,
            outputs=outputs,
            description=description
        )
        self.session = session

    async def prepare(self):
        """Pre-command preparation hook

        Override to perform setup before command execution.
        """
        pass

    async def work(self):
        """Execute command and process messages"""
        had_error = False

        await self.prepare()

        cmd = self.command_get()
        async for msg in self.session.interact(cmd):
            # Check for errors (skip non-ToolMessage objects like progress indicators)
            if hasattr(msg, 'severity'):
                had_error |= (msg.severity == MessageSeverity.ERROR)
            await self.message_handle(msg)
        if had_error:
            raise BuildError("Command generated error messages")

    async def message_handle(self, msg: ToolMessage) -> None:
        """Handle a message from the session

        Override to customize message handling.
        """
        await self.add_message_obj(msg)

    def command_get(self) -> CommandT:
        """Return the command to execute

        Override to provide the command for this task.
        """
        return None
