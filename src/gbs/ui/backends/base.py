"""Abstract base class for feedback backends

Backends are responsible for rendering user feedback messages to
various output formats (terminal, JSON, HTML, etc.).
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any

__all__ = ["FeedbackBackend"]


class FeedbackBackend(ABC):
    """Abstract base for feedback rendering backends

    Subclasses implement specific rendering strategies:
    - SimpleBackend: Plain text output for terminals/CI
    - RichBackend: Fancy terminal output with Rich library
    - JSONBackend: Structured JSON output for tools
    - HTMLBackend: HTML output for build reports
    """

    async def start(self):
        """Called when the FeedbackHub starts

        Use this to initialize resources (e.g., Rich Console, file handles).
        """
        pass

    async def stop(self):
        """Called when the FeedbackHub stops

        Use this to clean up resources and flush any buffered output.
        """
        pass

    def divert_output(self, stream):
        """Send this backend's normal output to `stream` instead.

        Used by commands whose own result goes to stdout and must stay
        machine readable: messages are moved aside so only the result
        is on the stream the caller reads. Backends that do not write
        to a terminal ignore it.
        """
        pass

    @abstractmethod
    async def render(self, msg: Any):
        """Render a message

        Args:
            msg: Message to render (ToolMessage, LogMessage, ProgressStart, etc.)

        This method is called by the FeedbackHub's async task for each message.
        It should render the message to the backend's output destination.
        """
        pass
