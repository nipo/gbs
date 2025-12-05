from __future__ import annotations
import asyncio
import logging
import re
from typing import AsyncIterator
from pathlib import Path

from ...build.task import Task
from ...build.context import BuildContext
from ...build.message import MessageSeverity, ToolMessage

__all__ = ["ProgressIndication", "Session", "GwShCommand", "LongRunningCommand"]

class ProgressIndication:
    """A progress indication specific to long-running tasks of gw_sh"""
    
    def __init__(self, percent, message):
        self.percent = percent
        self.message = message
    
class Session:
    """Shared gw_sh interactive session with command serialization

    Manages a persistent gw_sh subprocess that maintains synthesis state.
    All commands are serialized via asyncio.Lock since gw_sh is non-concurrent.
    """

    def __init__(self, gw_sh_executable: Path, work_dir: Path, logger: logging.Logger):
        self.gw_sh_executable = gw_sh_executable
        self.work_dir = work_dir
        self.logger = logger
        self.process: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()

    async def _ensure_started(self):
        """Start gw_sh subprocess if not already running"""
        if self.process is not None:
            return

        self.process = await asyncio.create_subprocess_exec(
            str(self.gw_sh_executable),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.work_dir,
        )

        await self._tcl_send("set tcl_interactive 1")
        async for msg in self._tcl_messages_receive():
            pass

    # Regex patterns for parsing Gowin output
    progress_pattern = re.compile(r'^\[(?P<pct>[0-9]+)%\] (?P<message>.*)$')
    msg_pattern = re.compile(r'^(?P<level>[A-Z]+) +\(?(?P<ex>[A-Z0-9]+)\)? ?: (?P<message>.*)$')
    file_pattern = re.compile(r'^(?P<explaination>[^\(]+)\("(?P<filename>[^"]+)":(?P<line>[0-9]+)\)$')
    prompt = "% "

    # Map Gowin levels to MessageSeverity
    severity_map = {
        'NOTE': MessageSeverity.NOTICE,
        'INFO': MessageSeverity.INFO,
        'WARN': MessageSeverity.WARNING,
        'WARNING': MessageSeverity.WARNING,
        'ERROR': MessageSeverity.ERROR,
        'FATAL': MessageSeverity.FATAL,
    }

    @classmethod
    def log_line_parse(cls, line: str) -> Optional[ToolMessage | ProgressIndication]:
        """Parse a line into a ToolMessage"""
        if not line:
            return None

        match = cls.progress_pattern.match(line)
        if match:
            return ProgressIndication(int(match.group("pct")), match.group("message"))
        
        # Try to match Gowin message format
        match = cls.msg_pattern.match(line)
        if not match:
            # Unstructured output - create DEBUG message
            return ToolMessage(
                severity=MessageSeverity.DEBUG,
                message=line,
            )

        level_str = match.group('level')
        ex_code = match.group('ex')
        message = match.group('message')

        # Get message severity
        severity = cls.severity_map.get(level_str, MessageSeverity.DEBUG)

        # Check if message contains file/line info
        file_match = cls.file_pattern.match(message)
        if not file_match:
            return ToolMessage(
                severity=severity,
                message=message,
                identifier=ex_code,
            )

        explanation = file_match.group('explaination')
        filename = file_match.group('filename')
        line_num = int(file_match.group('line'))

        return ToolMessage(
            severity=severity,
            message=explanation,
            identifier=ex_code,
            file_path=Path(filename),
            line=line_num,
        )

    async def _tcl_send(self, tcl_command: str) -> None:
        """Send TCL command to interpreter.

        This method should be called with lock held.
        """
        self.logger.debug(f"% {tcl_command}")

        # Send command with newline
        self.process.stdin.write(f"{tcl_command}\n".encode('utf-8'))
        await self.process.stdin.drain()

    async def _tcl_messages_receive(self) -> AsyncIterator[ToolMessage | ProgressIndication]:
        """Receive messages from the interpreter. End iteration when
        we see prompt.

        This method should be called with lock held.
        """
        buffer = ""
        while True:
            chunk = await self.process.stdout.read(1024)
            if not chunk:
                raise RuntimeError("gw_sh process terminated unexpectedly")

            buffer += chunk.decode('utf-8', errors='replace')

            while True:
                try:
                    line, buffer = buffer.split("\n", 1)
                except ValueError:
                    break

                msg = self.log_line_parse(line)
                if msg:
                    yield msg

            if buffer.lstrip().endswith(self.prompt):
                break
        
    async def command_interact(self, tcl_command: str) -> AsyncIterator[ToolMessage | ProgressIndication]:
        """Send TCL command and yield ToolMessage objects as they arrive (serialized)

        Parses Gowin output and creates ToolMessage instances for structured messages.
        Lines matching "LEVEL (EXnnnn) : message" are parsed into ToolMessage objects.
        Other lines are yielded as DEBUG-level ToolMessage objects.

        Args:
            tcl_command: TCL command to execute

        Yields:
            ToolMessage or gowin-specific progress instances for each line of output
        """

        async with self.lock:
            await self._ensure_started()
            await self._tcl_send(tcl_command)
            async for msg in self._tcl_messages_receive():
                yield msg

    async def close(self):
        """Shutdown gw_sh cleanly"""
        if self.process is not None:
            async with self.lock:
                try:
                    self.process.stdin.write(b"exit\n")
                    await self.process.stdin.drain()
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
                finally:
                    self.process = None


class GwShCommand(Task):
    def __init__(
            self,
            context: BuildContext,
            name: str,
            session: Session,
            inputs: list,
            outputs: list,
            description: str = "",
    ):
        super().__init__(context = context,
                         name = name,
                         inputs = inputs,
                         outputs = outputs,
                         description = description)
        self.session = session

    async def command_interact(self, tcl_command: str) -> AsyncIterator[ToolMessage]:
        """
        Helper that wraps shell task, iterate through messages
        """
        async for msg in self.session.command_interact(tcl_command):
            if isinstance(msg, ProgressIndication):
                await self.update_progress(msg.percent / 100, msg.message)
                continue
            await self.add_message_obj(msg)
            yield msg

    async def command_run(self, tcl_command: str) -> None:
        """
        Helper that runs a command and wait for completion
        """
        async for msg in self.command_interact(tcl_command):
            pass

class LongRunningCommand(GwShCommand):
    """Run Gowin long running via gw_sh"""

    async def work(self, command) -> None:
        """Run command via gw_sh (project already initialized)"""
        await self.command_run(command)

        self.logger.info(f"{command} finished")
