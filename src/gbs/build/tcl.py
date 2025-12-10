"""TCL interpreter interactive session

Provides TCL-specific session management and command classes for
interacting with TCL-based tools.
"""

from __future__ import annotations
from typing import Any

from .interactive import Session as BaseSession, CommandTask as BaseCommandTask
from .message import MessageSeverity, ToolMessage

__all__ = ["Session", "CommandTask", "SimpleCommandTask", "Expression", "String", "BareWord", "Command", "Expansion"]


class Expression:
    """Base class for TCL expressions"""

    def __init__(self, expr):
        self.expr = expr

    def __str__(self):
        return self.expr

    @classmethod
    def from_str(cls, s: Any) -> Expression:
        if isinstance(s, Expression):
            return s
        return String(str(s))


class String(Expression):
    """TCL string literal (brace-quoted)"""

    def __init__(self, value: str):
        self.value = str(value)

    def __str__(self):
        return f'{{{self.value}}}'


class BareWord(Expression):
    """TCL bare word (unquoted)"""

    def __init__(self, value: str):
        self.value = value

    def __str__(self):
        return self.value

    @classmethod
    def from_str(cls, s: Any) -> Expression:
        if isinstance(s, Expression):
            return s
        return BareWord(str(s))


class Command(Expression):
    """TCL command (space-separated arguments)"""

    def __init__(self, argv):
        self.argv = [BareWord.from_str(argv[0])] + [Expression.from_str(x) for x in argv[1:]]

    def __str__(self):
        return " ".join([str(x) for x in self.argv])


class Expansion(Command):
    """TCL command substitution (bracket-enclosed)"""

    def __init__(self, argv):
        super().__init__(argv)

    def __str__(self):
        return f'[{super().__str__()}]'


class Session(BaseSession[Command]):
    """TCL interpreter interactive session

    Manages a persistent TCL subprocess that maintains state between commands.
    """

    prompt: str = "% "

    def _cmd_serialize(self, cmd: Command) -> str:
        """Serialize TCL command to string"""
        return str(cmd)

    def _exit_command(self) -> Command:
        """Return TCL exit command"""
        return Command(["exit"])


class CommandTask(BaseCommandTask[Command]):
    """Task that executes TCL commands in a session

    Provides TCL-specific command execution infrastructure.
    """

    def command(self) -> Command:
        """Return the TCL command to execute

        Override to provide the command for this task.
        Deprecated: use command_get() instead for consistency.
        """
        return None

    def command_get(self) -> Command:
        """Return the TCL command to execute

        Override to provide the command for this task.
        """
        # Support both command() and command_get() for compatibility
        return self.command()


class SimpleCommandTask(CommandTask):
    """Simple TCL command task using class attributes

    Subclass and set class attributes:
        name: Task name
        description: Task description
        command: TCL Command to execute
    """

    name: str
    description: str
    command: Command

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

    def command_get(self) -> Command:
        return self.__class__.command
