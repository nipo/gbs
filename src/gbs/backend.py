"""Backend System for GBS - Compatibility Shim

This module provides backward compatibility by re-exporting from the new locations:
- Base classes from gbs.model.dispatcher
- Concrete dispatchers from gbs.backend.*

All new code should import from the new locations directly.
"""

from __future__ import annotations

# Re-export base classes from gbs.model.dispatcher
from .model.dispatcher import (
    Dispatcher,
    BaseDispatcher,
    DispatcherRegistry,
    run_dispatcher_iteration,
)

# Backward compatibility aliases
Backend = Dispatcher
BaseBackend = BaseDispatcher
BackendRegistry = DispatcherRegistry
run_backend_iteration = run_dispatcher_iteration

# Re-export concrete dispatchers from gbs.backend
from .backend.ghdl import GHDLDispatcher
from .backend.verilog_to_vhdl import VerilogToVHDLDispatcher
from .backend.mem_init import MemInitDispatcher

# Backward compatibility aliases for dispatchers
GHDLBackend = GHDLDispatcher
VerilogToVHDLBackend = VerilogToVHDLDispatcher
MemInitBackend = MemInitDispatcher

__all__ = [
    # New names (preferred)
    "Dispatcher",
    "BaseDispatcher",
    "DispatcherRegistry",
    "run_dispatcher_iteration",
    "GHDLDispatcher",
    "VerilogToVHDLDispatcher",
    "MemInitDispatcher",
    # Old names (backward compatibility)
    "Backend",
    "BaseBackend",
    "BackendRegistry",
    "run_backend_iteration",
    "GHDLBackend",
    "VerilogToVHDLBackend",
    "MemInitBackend",
]
