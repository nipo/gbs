"""Backend System for GBS - Compatibility Shim

This module provides backward compatibility by re-exporting from the new locations:
- Base classes from gbs.model.backend
- Concrete backends from gbs.backend.*

All new code should import from the new locations directly.
"""

from __future__ import annotations

# Re-export base classes from gbs.model.backend
from .model.backend import (
    Backend,
    BaseBackend,
    BackendRegistry,
    run_backend_iteration,
)

# Re-export concrete backends from gbs.backend
from .backend.ghdl import GHDLBackend
from .backend.verilog_to_vhdl import VerilogToVHDLBackend
from .backend.mem_init import MemInitBackend

__all__ = [
    # Base classes
    "Backend",
    "BaseBackend",
    "BackendRegistry",
    "run_backend_iteration",
    # Concrete backends
    "GHDLBackend",
    "VerilogToVHDLBackend",
    "MemInitBackend",
]
