"""GBS Backend Package

This package contains concrete backend implementations for the GBS build system.
Also re-exports base classes for convenience.
"""

# Re-export base classes from gbs.model.backend
from gbs.model.backend import (
    Backend,
    BaseBackend,
    BackendRegistry,
    run_backend_iteration,
)

# Re-export all concrete backends
from gbs.backend.ghdl import GHDLBackend
from gbs.backend.verilog_to_vhdl import VerilogToVHDLBackend
from gbs.backend.mem_init import MemInitBackend

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
