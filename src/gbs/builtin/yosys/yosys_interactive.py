"""Yosys interactive session management"""

from __future__ import annotations
import asyncio
import logging
import re
from typing import AsyncIterator
from pathlib import Path

from ...build.task import Task, BuildError
from ...build import shell
from ...build.message import MessageSeverity, ToolMessage

__all__ = ["Session"]

class Session(shell.Session):
    """Shared yosys interactive session with command serialization

    Manages a persistent yosys subprocess that maintains synthesis state.
    """

    prompt = "yosys> "

    def __init__(self, yosys_executable: str, work_dir: Path):
        bin_path = Path(yosys_executable).parent
        ghdl_lib_dir = bin_path / ".." / "lib" / "ghdl"
        super().__init__(argv = [yosys_executable],
                         cwd = work_dir,
                         env = {"GHDL_PREFIX":str(ghdl_lib_dir)})

    async def session_init(self):
        await super().session_init()
        await self.execute(["plugin", "-i", "ghdl"])
        
    # Regex patterns for parsing yosys output
    # Yosys format: "level: message" or just plain output
    msg_pattern = re.compile(r'^(?P<level>Info|Warning|Error|Fatal):\s+(?P<message>.*)$', re.IGNORECASE)

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
            if not match:
                # Unstructured output - create DEBUG message
                yield ToolMessage(
                    severity=MessageSeverity.DEBUG,
                    message=line,
                )
                continue

            level_str = match.group('level').lower()
            message = match.group('message')

            # Get message severity
            severity = self.severity_map.get(level_str, MessageSeverity.DEBUG)

            yield ToolMessage(
                severity=severity,
                message=message,
            )

    stderr_transform = stdout_transform
