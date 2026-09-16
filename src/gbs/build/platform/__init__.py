"""Platform abstraction layer for OS-specific build operations.

Provides unified APIs for:
- Pseudo-terminal (PTY) creation and async I/O
- Process tree management (kill process groups/trees)
"""

import sys

from ._process_info import ProcessInfo

if sys.platform == "win32":
    from ._windows import PtyProvider, ProcessControl, wrap_bat_argv
else:
    from ._unix import PtyProvider, ProcessControl, wrap_bat_argv

__all__ = ["PtyProvider", "ProcessControl", "ProcessInfo", "wrap_bat_argv"]
