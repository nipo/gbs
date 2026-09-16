"""Process description shared by the platform implementations.

Lives in its own module because the Windows implementation cannot import
the Unix one (which pulls in the pty module) and vice-versa.
"""

from __future__ import annotations
from typing import NamedTuple

__all__ = ["ProcessInfo"]


class ProcessInfo(NamedTuple):
    """One live process, as reported by the platform process lister."""

    pid: int
    name: str
    session: int
    state: str
    age: float
    """Seconds since the process started."""
    starttime: int
    """Platform-specific start timestamp, unique per (pid, incarnation)."""
