"""Backward compatibility shim for build.message

This module has been moved to gbs.ui.messages. This file provides
backward compatibility imports for existing code.

New code should import from gbs.ui instead:
    from gbs.ui import MessageSeverity, ToolMessage
"""

from __future__ import annotations

# Import from new location
from ..ui.messages import MessageSeverity, ToolMessage

__all__ = ["MessageSeverity", "ToolMessage"]
