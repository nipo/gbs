"""Backend system

This module provides backend registry and dispatcher iteration utilities.

For protocols, import from gbs.protocol
For base classes, import from gbs.base
"""

from .registry import BackendRegistry, get_backend_registry, DispatcherRegistry, run_dispatcher_iteration

__all__ = ["BackendRegistry", "get_backend_registry", "DispatcherRegistry", "run_dispatcher_iteration"]
