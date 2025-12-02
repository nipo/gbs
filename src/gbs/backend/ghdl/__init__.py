"""GHDL Backend for GBS

This module implements the GHDL backend that compiles VHDL designs.
Supports both mcode and compiled (GCC/LLVM) GHDL backends.
"""

from __future__ import annotations
from pathlib import Path
from ...model.build import BuildContext, BuildFileSet, BuildResource
from .dispatcher import GHDLDispatcher
from . import backend


def get_backend():
    """Get the GHDL backend for build planning and execution

    Returns:
        GHDLBackend instance implementing the Backend Protocol
    """
    return backend.GHDLBackend()
