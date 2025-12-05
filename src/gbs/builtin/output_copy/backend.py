"""Generic output copy backend"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.protocol import BaseBackend
from ...backend.dispatcher import Dispatcher
from .dispatcher import OutputCopyDispatcher

class OutputCopyBackend(BaseBackend):
    """Generic output result copy backend
    """

    def __init__(self):
        super().__init__("gbs.builtin.output_copy")

    def create_dispatcher(self, config: dict[str, Any]) -> Dispatcher:
        """Create output copier dispatcher

        Args:
            config: Backend configuration (empty)

        Returns:
            OutputCopyDispatcher instance
        """
        return OutputCopyDispatcher()
