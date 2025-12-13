from __future__ import annotations
import re
from typing import AsyncIterator
from pathlib import Path

from ...build import tcl
from ...build.message import MessageSeverity, ToolMessage

__all__ = ["ProgressIndication", "Session", "GwShCommand", "LongRunningCommand"]

class ProgressIndication:
    """A progress indication specific to long-running tasks of gw_sh"""

    def __init__(self, percent, message):
        self.percent = percent
        self.message = message

class Session(tcl.Session):
    """Shared gw_sh interactive session with command serialization

    Manages a persistent gw_sh subprocess that maintains synthesis state.
    Extends the generic TCL session with Gowin-specific message parsing.
    """

    # Gowin-specific prompt
    prompt = "% "

    # Regex patterns for parsing Gowin output
    progress_pattern = re.compile(r'^\[(?P<pct>[0-9]+)%\] (?P<message>.*)$')
    msg_pattern = re.compile(r'^(?P<level>[A-Z]+) +\(?(?P<ex>[A-Z0-9]+)\)? ?: (?P<message>.*)$')
    file_pattern = re.compile(r'^(?P<explaination>.+?)\("(?P<filename>[^"]+)":(?P<line>[0-9]+)\)$')

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
    def log_line_parse(cls, line: str) -> ToolMessage | ProgressIndication | None:
        """Parse a line into a ToolMessage or ProgressIndication"""
        if not line:
            return None

        # Check for progress indication
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

    async def stdout_transform(self, lines: AsyncIterator[str]) -> AsyncIterator[ToolMessage | ProgressIndication]:
        """Transform stdout lines into ToolMessage or ProgressIndication objects

        Overrides the base tcl.Session stdout transformer to handle Gowin-specific
        output formats including progress indicators and structured error messages.
        """
        async for line in lines:
            msg = self.log_line_parse(line)
            if msg:
                yield msg


class GwShCommand(tcl.CommandTask):
    """Base task class for Gowin gw_sh commands

    Wraps the generic TCL CommandTask with Gowin-specific progress handling.
    Converts ProgressIndication objects into task progress updates.
    """

    async def message_handle(self, msg: ToolMessage | ProgressIndication) -> None:
        """Handle messages from gw_sh, including progress indications"""
        if isinstance(msg, ProgressIndication):
            await self.update_progress(msg.percent / 100, msg.message)
        else:
            await self.add_message_obj(msg)

    async def command_run(self, cmd: tcl.Command) -> None:
        """Run a TCL command and wait for completion

        Args:
            cmd: TCL Command object to execute
        """
        async for msg in self.session.interact(cmd):
            await self.message_handle(msg)


class LongRunningCommand(GwShCommand):
    """Convenience class for long-running Gowin commands

    Simplifies running a single TCL command via gw_sh.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        name: str,
        session: Session,
        command: tcl.Command,
        inputs: list,
        outputs: list,
        description: str = "",
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=name,
            session=session,
            inputs=inputs,
            outputs=outputs,
            description=description,
        )
        self._command = command

    def command(self) -> tcl.Command:
        """Return the command to execute"""
        return self._command
