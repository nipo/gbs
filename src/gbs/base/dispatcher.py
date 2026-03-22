"""Base Dispatcher Implementation

Abstract base class for dispatchers. Subclass this to create new dispatchers.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..ui.reporter import UIReporter

if TYPE_CHECKING:
    from ..build.context import BuildContext

__all__ = ["BaseDispatcher"]


class BaseDispatcher(UIReporter, ABC):
    """Base class for dispatchers

    Provides common functionality and enforces the Dispatcher protocol.
    Subclasses must implement process().

    Inherits from UIReporter to provide logging and progress reporting.

    Attributes:
        context: Build context for this realization
        name: Unique dispatcher name
        tool_name: Tool identifier for configuration lookup
    """

    def __init__(self, context: BuildContext, name: str, tool_name: str):
        """Initialize dispatcher

        Args:
            context: Build context for this realization
            name: Unique dispatcher name
            tool_name: Tool identifier for configuration lookup
        """
        self.context = context
        self.name = name
        self.tool_name = tool_name

        # Initialize UIReporter with BuildContext as parent
        UIReporter.__init__(self,
            reporter_name=f"Dispatcher({name})",
            parent_reporter=context
        )

    @property
    def tool_config(self) -> dict | None:
        """This dispatcher's tool configuration.

        Looks up tool_name in the GBS config. Used by tasks for
        tool-specific settings like executable paths and message
        level overrides.
        """
        return self.context.get_tool(self.tool_name, required=False)

    def attach_definition_dependencies(self, task: 'Task') -> None:
        """Attach all DEFINITION resources as non-consuming inputs to a task.

        This makes the task re-run when any build definition file changes
        (project file, GBS configs, repository definitions, config fingerprint).
        """
        from ..build.task import ResourceTypology
        for resource in self.context.filter_pending(typology=ResourceTypology.DEFINITION):
            task.add_input(resource, consume=False)

    @abstractmethod
    async def process(self) -> None:
        """Process the pending work queue

        Must be implemented by subclasses.
        Use self.context.filter_pending(), self.context.add_pending(),
        self.context.remove_pending(), etc.
        """
        ...

    def get_clean_paths(self) -> set:
        """Return paths that should be cleaned by this dispatcher

        Default implementation returns the output path for this build context.
        Subclasses can override to clean additional or different paths.

        Returns:
            Set of Path objects to clean
        """
        return {self.context.output_path}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name})"
