"""Dispatcher Protocol

Pure protocol definition for dispatchers that transform the pending work queue.
This is used for type checking only - implementations should inherit from
gbs.base.BaseDispatcher instead.
"""

from __future__ import annotations
from typing import Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from ..build.context import BuildContext

__all__ = ["Dispatcher"]


@runtime_checkable
class Dispatcher(Protocol):
    """Protocol for dispatchers that transform the pending work queue

    All dispatchers must implement:
    - name: Unique identifier
    - context: BuildContext reference (provided to constructor)
    - process(): Transform the pending work queue
    - get_clean_paths(): Return paths to clean

    Attributes:
        name: Unique dispatcher identifier
        context: Build context for this realization
    """

    name: str
    tool_name: str
    context: BuildContext

    async def process(self) -> None:
        """Process the pending work queue, transforming it in place"""
        ...

    def get_clean_paths(self) -> set:
        """Return paths that should be cleaned by this dispatcher"""
        ...

    @property
    def tool_config(self) -> dict | None:
        """This dispatcher's tool configuration"""
        ...
