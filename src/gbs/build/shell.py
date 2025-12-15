"""Shell interactive session

Provides shell-specific session management for interacting with
shell-based tools and commands.
"""

from __future__ import annotations
import shlex

from .interactive import Session as BaseSession, CommandTask as BaseCommandTask
from ..ui.messages import MessageSeverity, ToolMessage

__all__ = ["Session", "CommandTask", "SimpleCommandTask"]


class Session(BaseSession[list[str]]):
    """Shell interactive session

    Manages a persistent shell subprocess that maintains state between commands.
    Commands are provided as list of strings and serialized using shlex.join.
    """

    prompt: str = "$ "

    def _cmd_serialize(self, cmd: list[str]) -> str:
        """Serialize shell command to string using shlex"""
        return shlex.join(cmd)

    def _exit_command(self) -> list[str]:
        """Return shell exit command"""
        return ["exit"]


class CommandTask(BaseCommandTask[list[str]]):
    """Task that executes shell commands in a session

    Provides shell-specific command execution infrastructure.
    """
    pass


class SimpleCommandTask(CommandTask):
    """Simple shell command task using class attributes

    Subclass and set class attributes:
        name: Task name
        description: Task description
        command: Shell command as list of strings
    """

    name: str
    description: str
    command: list[str]

    def __init__(
            self,
            dispatcher: "Dispatcher",
            session: Session,
            inputs=None,
            outputs=None,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=self.__class__.name,
            session=session,
            inputs=inputs or [],
            outputs=outputs or [],
            description=self.__class__.description,
        )

    def command_get(self) -> list[str]:
        return self.__class__.command
