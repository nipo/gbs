"""GBS Protocols

Pure protocol definitions for type checking. These define the interfaces
that GBS components must implement but should not be inherited directly.

For implementation, use the base classes from gbs.base instead.
"""

from .backend import Backend
from .dispatcher import Dispatcher
from .pass_ import Pass
from .plugin import Plugin
from .toolchain import ToolchainProvider

__all__ = ["Backend", "Dispatcher", "Pass", "Plugin", "ToolchainProvider"]
