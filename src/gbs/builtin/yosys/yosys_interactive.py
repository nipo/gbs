"""Yosys interactive session management"""

from __future__ import annotations
import asyncio
import logging
import re
from typing import AsyncIterator
from pathlib import Path

from ...build.task import Task, BuildError
from ...build import shell
from ...ui.messages import MessageSeverity, ToolMessage

__all__ = ["Session"]

class Session(shell.Session):
    """Shared yosys interactive session with command serialization

    Manages a persistent yosys subprocess that maintains synthesis state.
    """

    prompt = "yosys> "

    def __init__(self, yosys_executable: str, work_dir: Path, extra_env: dict[str, str] = None):
        bin_path = Path(yosys_executable).parent
        ghdl_lib_dir = bin_path / ".." / "lib" / "ghdl"
        env = {"GHDL_PREFIX": str(ghdl_lib_dir)}
        if extra_env:
            env.update(extra_env)
        super().__init__(argv = [yosys_executable],
                         cwd = work_dir,
                         env = env)

    async def session_init(self):
        await super().session_init()
        await self.execute(["plugin", "-i", "ghdl"])
        
    # Regex patterns for parsing yosys output
    # Yosys format: "level: message" or just plain output
    msg_pattern = re.compile(r'^(?P<level>Info|Warning|Error|Fatal):\s+(?P<message>.*)$', re.IGNORECASE)
    # Yosys/GHDL format: "path:line:column:level: message"
    msg_pattern_ghdl = re.compile(r'^(?P<path>.*):(?P<line>[0-9]+):(?P<column>[0-9]+):(?P<level>info|warning|error|fatal):\s+(?P<message>.*)$', re.IGNORECASE)

    # Map yosys levels to MessageSeverity
    severity_map = {
        'info': MessageSeverity.INFO,
        'warning': MessageSeverity.WARNING,
        'error': MessageSeverity.ERROR,
        'fatal': MessageSeverity.FATAL,
    }

    async def stdout_transform(self, lines: AsyncIterator[str]) -> AsyncIterator[ToolMessage]:
        """Parse a lines into ToolMessages"""
        async for line in lines:
            if not line:
                continue

            # Try to match yosys message format
            match = self.msg_pattern.match(line)
            if match:
                level_str = match.group('level').lower()
                message = match.group('message')

                # Get message severity
                severity = self.severity_map.get(level_str, MessageSeverity.DEBUG)

                yield ToolMessage(
                    severity=severity,
                    message=message,
                )
                continue

            # Try to match yosys/GHDL message format
            match = self.msg_pattern_ghdl.match(line)
            if match:
                level_str = match.group('level').lower()
                message = match.group('message')
                path = match.group('path')
                line = int(match.group('line'))
                column = int(match.group('column'))

                # Get message severity
                severity = self.severity_map.get(level_str, MessageSeverity.DEBUG)

                yield ToolMessage(
                    severity=severity,
                    message=message,
                    file_path=Path(path),
                    line=line,
                    column=column,
                )
                continue

            # Unstructured output - create DEBUG message
            yield ToolMessage(
                severity=MessageSeverity.DEBUG,
                message=line,
            )
            continue


    stderr_transform = stdout_transform
