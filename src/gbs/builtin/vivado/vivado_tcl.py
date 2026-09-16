"""Vivado TCL session and command helpers

Provides a specialized TCL session for interacting with Vivado in
batch/TCL mode. Handles Vivado-specific message parsing and progress
reporting.
"""

from __future__ import annotations
import re
from typing import AsyncIterator
from pathlib import Path

from ...build import tcl
from ...build.platform import ProcessControl, ProcessInfo
from ...build.process_watchdog import ProcessWatchdog
from ...ui.messages import MessageSeverity, ToolMessage

__all__ = ["ProgressIndication", "Session", "VivadoCommand", "LongRunningCommand"]


class ProgressIndication:
    """A progress indication specific to long-running Vivado tasks"""

    def __init__(self, phase: str, step: str | None = None):
        self.phase = phase
        self.step = step


class Session(tcl.Session):
    """Shared Vivado TCL interactive session with command serialization

    Manages a persistent Vivado TCL subprocess that maintains synthesis state.
    Extends the generic TCL session with Vivado-specific message parsing.

    Vivado is launched with: vivado -mode tcl -nojournal -nolog
    """

    # Vivado TCL prompt
    prompt = "Vivado% "

    # Vivado's source scanning helper, and how long it may run before
    # being considered stalled
    srcscanner_name = "srcscanner"
    srcscanner_grace = 20.0

    # Regex patterns for parsing Vivado output
    # Vivado messages format: SEVERITY: [ID] message
    msg_pattern = re.compile(
        r'^(?P<severity>INFO|WARNING|CRITICAL WARNING|ERROR): '
        r'\[(?P<id>[^\]]+)\] (?P<message>.*)$'
    )
    # Phase indication: e.g., "Phase 1 Retarget"
    phase_pattern = re.compile(r'^Phase (?P<num>\d+) (?P<name>.+)$')
    # Step indication
    step_pattern = re.compile(r'^\s+Step (?P<num>\d+\.\d+) (?P<name>.+)$')

    # Map Vivado severity levels to MessageSeverity
    severity_map = {
        'INFO': MessageSeverity.INFO,
        'WARNING': MessageSeverity.WARNING,
        'CRITICAL WARNING': MessageSeverity.WARNING,
        'ERROR': MessageSeverity.ERROR,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._srcscanner_watchdog = None

    @classmethod
    def log_line_parse(cls, line: str) -> ToolMessage | ProgressIndication | None:
        """Parse a line into a ToolMessage or ProgressIndication"""
        if not line or line.isspace():
            return None

        # Check for phase indication
        match = cls.phase_pattern.match(line)
        if match:
            return ProgressIndication(
                phase=f"Phase {match.group('num')}: {match.group('name')}"
            )

        # Check for step indication
        match = cls.step_pattern.match(line)
        if match:
            return ProgressIndication(
                phase="",
                step=f"Step {match.group('num')}: {match.group('name')}"
            )

        # Try to match Vivado message format
        match = cls.msg_pattern.match(line)
        if not match:
            # Unstructured output - create DEBUG message
            return ToolMessage(
                severity=MessageSeverity.DEBUG,
                message=line,
            )

        severity_str = match.group('severity')
        msg_id = match.group('id')
        message = match.group('message')

        # Get message severity
        severity = cls.severity_map.get(severity_str, MessageSeverity.DEBUG)

        return ToolMessage(
            severity=severity,
            message=message,
            identifier=msg_id,
        )

    async def stdout_transform(self, lines: AsyncIterator[str]) -> AsyncIterator[ToolMessage | ProgressIndication]:
        """Transform stdout lines into ToolMessage or ProgressIndication objects

        Overrides the base tcl.Session stdout transformer to handle Vivado-specific
        output formats including progress indicators and structured error messages.
        """
        async for line in lines:
            msg = self.log_line_parse(line)
            if msg:
                yield msg

    async def session_init(self):
        """Initialize Vivado TCL session

        Sends initial setup commands to Vivado after the process starts.
        """
        # Wait for initial prompt
        await super().session_init()

        self._srcscanner_watchdog_start()

    def _srcscanner_watchdog_start(self):
        """Start watching for stalled srcscanner helpers

        Vivado spawns srcscanner when sources are added or the top cell is
        set. It sometimes loops forever and the TCL prompt never returns.
        Its result is not needed: GBS passes sources in dependency order.
        """
        if self._srcscanner_watchdog is not None:
            return

        if not ProcessControl.can_list_processes:
            self._logger.debug(
                "Process listing unavailable on this platform, "
                "srcscanner watchdog disabled"
            )
            return

        self._srcscanner_watchdog = ProcessWatchdog(
            root_pid=self._process.pid,
            process_name=self.srcscanner_name,
            grace_seconds=self.srcscanner_grace,
            on_kill=self._srcscanner_killed,
            root_alive=lambda: (self._process is not None
                                and self._process.returncode is None),
        )
        self._srcscanner_watchdog.start()

    def _srcscanner_killed(self, info: ProcessInfo):
        """Report a srcscanner kill to the command in flight

        Queueing the message rather than taking the session lock: the
        watchdog runs while interact() holds it.
        """
        self._logger.warning(
            f"Killed stalled {info.name} (pid {info.pid}) "
            f"after {info.age:.0f} s"
        )
        self._queue.put_nowait(ToolMessage(
            severity=MessageSeverity.WARNING,
            identifier="GBS-SRCSCANNER",
            message=(
                f"Killed stalled srcscanner (pid {info.pid}) after "
                f"{info.age:.0f} s; Vivado continues with the compilation "
                f"order set by GBS"
            ),
        ))

    async def close(self):
        """Stop the watchdog, then shut the session down"""
        watchdog = self._srcscanner_watchdog
        self._srcscanner_watchdog = None
        if watchdog is not None:
            await watchdog.stop()

        await super().close()


class VivadoCommand(tcl.CommandTask):
    """Base task class for Vivado TCL commands

    Wraps the generic TCL CommandTask with Vivado-specific progress handling.
    Converts ProgressIndication objects into task progress updates.
    """

    async def message_handle(self, msg: ToolMessage | ProgressIndication) -> None:
        """Handle messages from Vivado, including progress indications"""
        if isinstance(msg, ProgressIndication):
            status = msg.phase
            if msg.step:
                status = f"{msg.phase} - {msg.step}" if msg.phase else msg.step
            await self.update_progress(None, status)
        else:
            await self.add_message_obj(msg)

    async def command_run(self, cmd: tcl.Command) -> None:
        """Run a TCL command and wait for completion

        Args:
            cmd: TCL Command object to execute
        """
        rsp = []
        async for msg in self.session.interact(cmd):
            if isinstance(msg, ToolMessage):
                rsp.append(msg.line)
            await self.message_handle(msg)
        return rsp


class LongRunningCommand(VivadoCommand):
    """Convenience class for long-running Vivado commands

    Simplifies running a single TCL command via Vivado.
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
