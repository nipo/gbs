"""Lattice Diamond Tcl console (diamondc) session handling

Manages a persistent diamondc subprocess and parses its message
formats into ToolMessage objects.
"""

from __future__ import annotations
import re
from typing import AsyncIterator
from pathlib import Path

from ...build import tcl
from ...build.task import BuildError
from ...ui.messages import MessageSeverity, ToolMessage

__all__ = ["Session", "DiamondCommand"]


class Session(tcl.Session):
    """Shared diamondc interactive session

    diamondc is a Tcl console; it only emits its "% " prompt when driven
    through a pty, hence use_pty must be set by the creator.

    Extends the generic TCL session with Diamond-specific message parsing.
    Diamond tools (LSE, map, par, bitgen, ...) share the message shape::

        LEVEL - origin: /path/to/file.vhd(12): message text. CODE-1234
    """

    prompt = "% "

    msg_pattern = re.compile(
        r'^(?P<level>NOTE|INFO|WARNING|ERROR|FATAL) - +(?:(?P<origin>[a-zA-Z0-9_]+): )?(?P<message>.*)$')
    code_pattern = re.compile(
        r'^(?P<message>.*?) (?P<code>[A-Z][A-Z0-9]*-[0-9]+)$')
    file_pattern = re.compile(
        r'^(?P<filename>/[^(]+)\((?P<line>[0-9]+)\): (?P<message>.*)$')
    # LSE constraint checker format: /path/to/file.lpf:12:warning: message
    file_level_pattern = re.compile(
        r'^(?P<filename>/[^:]+):(?P<line>[0-9]+):(?P<level>info|warning|error): (?P<message>.*)$')

    severity_map = {
        'NOTE': MessageSeverity.NOTICE,
        'INFO': MessageSeverity.INFO,
        'WARNING': MessageSeverity.WARNING,
        'ERROR': MessageSeverity.ERROR,
        'FATAL': MessageSeverity.FATAL,
    }

    @classmethod
    def log_line_parse(cls, line: str) -> ToolMessage | None:
        """Parse a diamondc output line into a ToolMessage"""
        if not line:
            return None

        match = cls.msg_pattern.match(line)
        if not match:
            file_match = cls.file_level_pattern.match(line)
            if file_match:
                return ToolMessage(
                    severity=cls.severity_map[file_match.group('level').upper()],
                    message=file_match.group('message'),
                    file_path=Path(file_match.group('filename')),
                    line=int(file_match.group('line')),
                )
            # Unstructured output (tool banners, tables, progress text)
            return ToolMessage(
                severity=MessageSeverity.DEBUG,
                message=line,
            )

        severity = cls.severity_map[match.group('level')]
        origin = match.group('origin')
        message = match.group('message')

        identifier = None
        code_match = cls.code_pattern.match(message)
        if code_match:
            message = code_match.group('message')
            identifier = code_match.group('code')

        file_path = None
        line_num = None
        file_match = cls.file_pattern.match(message)
        if file_match:
            message = file_match.group('message')
            file_path = Path(file_match.group('filename'))
            line_num = int(file_match.group('line'))

        return ToolMessage(
            severity=severity,
            message=message,
            identifier=identifier,
            origin=origin,
            file_path=file_path,
            line=line_num,
        )

    async def stdout_transform(self, lines: AsyncIterator[str]) -> AsyncIterator[ToolMessage]:
        """Transform diamondc stdout lines into ToolMessage objects"""
        async for line in lines:
            msg = self.log_line_parse(line)
            if msg:
                yield msg


class DiamondCommand(tcl.CommandTask):
    """Base task class for diamondc commands

    Tcl-level failures do not propagate through the session, so each
    command raises when Diamond emitted error messages while running it.
    """

    async def command_run(self, cmd: tcl.Command) -> None:
        """Run a TCL command, wait for completion, raise on tool errors

        Args:
            cmd: TCL Command object to execute
        """
        had_error = False
        async for msg in self.session.interact(cmd):
            had_error |= msg.severity in (MessageSeverity.ERROR, MessageSeverity.FATAL)
            await self.add_message_obj(msg)
        if had_error:
            raise BuildError(f"'{cmd}' generated error messages")
