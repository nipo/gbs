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

    _TOOL_OPTION_MISSING = object()  # sentinel for missing default

    def get_tool_option(self, key: str, default=_TOOL_OPTION_MISSING):
        """Get a tool configuration option.

        Looks up tool_name in GBS config, then returns config[key].

        If the tool is not configured:
        - With default: returns default
        - Without default: raises MissingToolError with config hint

        If the tool IS configured but key is missing:
        - With default: returns default
        - Without default: raises KeyError

        Args:
            key: Config key to look up (e.g., "path", "executable")
            default: Default value if tool is not configured

        Examples:
            path = self.get_tool_option("path")  # raises if not configured
            exe = self.get_tool_option("executable", "yosys")  # fallback
        """
        config = self.context.get_tool(self.tool_name, required=False)
        if config is None:
            if default is not self._TOOL_OPTION_MISSING:
                return default
            from ..build.task import MissingToolError
            name_part = self.tool_name.split(':')[0] if ':' in self.tool_name else self.tool_name
            variant_part = self.tool_name.split(':', 1)[1] if ':' in self.tool_name else None
            hint = f"  tools:\n    - name: {name_part}\n"
            if variant_part:
                hint += f"      variant: {variant_part}\n"
            hint += f"      config:\n        path: /path/to/{name_part}\n"
            raise MissingToolError(
                f"Tool '{self.tool_name}' is not configured.\n"
                f"Add it to ~/.config/gbs.yaml or .gbs.yaml:\n{hint}"
            )
        return config.get(key, default) if default is not self._TOOL_OPTION_MISSING else config[key]

    @property
    def tool_config(self) -> dict | None:
        """Raw tool configuration dict, or None. Prefer get_tool_option()."""
        return self.context.get_tool(self.tool_name, required=False)

    @property
    def tool_env(self) -> dict[str, str]:
        """Environment variables from tool configuration.

        Returns the 'env' dict from the tool config, or empty dict
        if not configured. Backends should merge this into the
        environment passed to subprocesses and interactive sessions.

        Example GBS config::

            tools:
              - name: vivado
                variant: 2022.2
                config:
                  path: /opt/Xilinx/Vivado/2022.2
                  env:
                    LM_LICENSE_FILE: 1700@license-server
                    XILINXD_LICENSE_FILE: /path/to/license.lic
        """
        return self.get_tool_option("env", {})

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

    async def close(self) -> None:
        """Clean up resources (sessions, subprocesses).

        Called by the build context after build completion. Override
        to close interactive sessions and kill any spawned processes.
        Default implementation does nothing.
        """
        pass

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
