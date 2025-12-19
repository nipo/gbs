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
    context: BuildContext

    async def process(self) -> None:
        """Process the pending work queue, transforming it in place

        Dispatchers can:
        - Add new generated files (e.g., transpiled outputs) via self.context.add_pending()
        - Remove processed files (e.g., inputs that were transformed) via self.context.remove_pending()
        - Create build tasks for files
        - Query and filter pending resources via self.context.filter_pending()

        The pending queue is modified in place. The modification serial will be
        used to detect convergence.

        Note:
            This is an async method to support task creation and other
            async operations. Access context via self.context.
        """
        ...

    def get_clean_paths(self) -> set:
        """Return paths that should be cleaned by this dispatcher

        Returns:
            Set of Path objects to clean. Typically includes self.context.output_path
            or subdirectories within it that this dispatcher creates.
        """
        ...

    def get_tool(self, name: str, required: bool = True) -> dict:
        """Get tool configuration from context

        Helper method that delegates to self.context.get_tool().

        Args:
            name: Tool identifier
            required: If True, raise error if tool not found

        Returns:
            Tool configuration dictionary
        """
        ...
