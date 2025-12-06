from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

__all__ = ["MessageSeverity", "ToolMessage"]

class MessageSeverity(Enum):
    """Severity levels for tool messages"""
    DEBUG = "debug"
    NOTICE = "notice"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"

    def __str__(self) -> str:
        return self.value

@dataclass
class ToolMessage:
    """Standardized message from EDA tools and backends

    Provides a homogeneous representation of messages from various tools,
    including errors, warnings, and informational messages.
    """
    severity: MessageSeverity
    message: str
    identifier: Optional[str] = None
    extended_message: Optional[str] = None
    file_path: Optional[Path] = None
    line: Optional[int] = None
    column: Optional[int] = None
    origin: "BuildStep" = None

    def __str__(self) -> str:
        """Format message for display"""
        if self.file_path:
            location = str(self.file_path)
            if self.line is not None:
                location += f":{self.line}"
                if self.column is not None:
                    location += f":{self.column}"
            parts = [f"{location}:{self.severity.value.upper()}:"]
        else:
            parts = [f"[{self.severity.value.upper()}]"]

        if self.identifier:
            parts.append(f"({self.identifier})")

        parts.append(self.message)

        result = " ".join(parts)

        if self.extended_message:
            result += "\n" + self.extended_message

        return result

    def pprint(self):
        print(str(self))
