"""GBS Base Classes

Abstract base classes for implementing GBS components. These provide
common functionality and enforce the protocol contracts.

Inherit from these when creating new backends, dispatchers, passes, or plugins.
"""

from .backend import BaseBackend
from .dispatcher import BaseDispatcher
from .pass_ import BasePass
from .plugin import BasePlugin
from .toolchain import BaseToolchainProvider

__all__ = ["BaseBackend", "BaseDispatcher", "BasePass", "BasePlugin", "BaseToolchainProvider"]
