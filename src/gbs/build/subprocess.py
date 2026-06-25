"""This module defines tools for handling subprocesses in the context
of gbs using asyncio.
"""

from __future__ import annotations
from collections import deque
from pathlib import Path
import asyncio
import os
import shlex
from ..ui.messages import MessageSeverity, ToolMessage
from ..ui.reporter import UIReporter
from .platform import wrap_bat_argv
from .task import ToolFailure

__all__ = ["MessageSubprocess"]

OUTPUT_TAIL_LINES = 30

class MessageSubprocess(UIReporter):
    """
    A subprocess that generates a stream of ToolMessage objects.

    Typical usage:

    proc = MessageSubprocess(["ls", "-1", "/tmp"])

    async for message in proc:
        # do something
        pass

    # proc.returncode has the exit status

    """
    def __init__(self,
                 argv: list[str],
                 cwd: Path = Path("."),
                 env: dict[str, str] | None = None,
                 parent_reporter: 'UIReporter' | None = None):
        """Initialize MessageSubprocess

        Args:
            argv: Command and arguments to execute
            cwd: Working directory for the subprocess
            env: Additional environment variables to inject (merged with current environment)
            parent_reporter: Optional parent UIReporter (typically the Task creating this subprocess)
        """
        # Initialize UIReporter with parent for progress nesting
        UIReporter.__init__(self,
            reporter_name=f"MessageSubprocess({argv[0] if argv else 'unknown'})",
            parent_reporter=parent_reporter
        )

        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.process = None
        self.__queue = asyncio.Queue()
        self.__output_tail: deque[str] = deque(maxlen=OUTPUT_TAIL_LINES)

    async def __aiter__(self) -> AsyncIterator[ToolMessage]:
        """Asynchronous iterator of messages. Messages from stdout and
        stderr transformers are mixed in the same iteration.  When
        iterator completes, process has exited.
        """
        await self.__launch()

        to_close = 2
        while to_close:
            msg = await self.__queue.get()
            if msg is None:
                to_close -= 1
                continue
            yield msg

        await self.process.wait()
        await self.stdout_task
        await self.stderr_task

    @property
    def returncode(self):
        if self.process:
            return self.process.returncode
        return None

    async def stdout_transform(self, lines: AsyncIterator[str]) -> AsyncIterator[ToolMessage]:
        """Transformer that receives lines of text from stdout one by
        one, may create a message from it.

        This may be overridden for custom message processing
        """
        async for line in lines:
            yield ToolMessage(severity = MessageSeverity.INFO,
                              message = line)

    async def stderr_transform(self, lines: AsyncIterator[str]) -> AsyncIterator[ToolMessage]:
        """Transformer that receives lines of text from stderr one by
        one, may create a message from it.

        This may be overridden for custom message processing
        """
        async for line in lines:
            yield ToolMessage(severity = MessageSeverity.ERROR,
                              message = line)

    async def __stream_liner(self, stream, stream_tag: str) -> AsyncIterator[str]:
        """Internal tool that takes a stream and yeilds it line by
        line. The final un-terminated chunk, if any, is yielded as a
        line as well so callers never lose the tail of the output.

        Each line is also recorded in the output tail buffer with its
        stream tag, so a later failure() can attach the last lines of
        the tool's raw output to a ToolFailure.
        """
        buffer = ""
        while True:
            chunk = await stream.read(1024)
            if not chunk:
                if buffer:
                    self.__output_tail.append(f"{stream_tag}: {buffer}")
                    yield buffer
                break

            buffer += chunk.decode('utf-8', errors='replace')

            while True:
                try:
                    line, buffer = buffer.split("\n", 1)
                except ValueError:
                    break
                self.__output_tail.append(f"{stream_tag}: {line}")
                yield line

    async def __stream_handler(self, stream, transformer, stream_tag: str):
        """Internal tool that takes a stream and puts it to queue.

        The completion sentinel is posted in a finally block so a
        crashing transformer cannot deadlock the consumer iterating
        __aiter__ on this subprocess.
        """
        try:
            async for msg in transformer(self.__stream_liner(stream, stream_tag)):
                await self.__queue.put(msg)
        finally:
            await self.__queue.put(None)

    async def __launch(self):
        if self.process:
            return

        # Render argv as a sh-compatible command line so the log entry
        # can be copy/pasted directly into a terminal to reproduce.
        cmd = " ".join(shlex.quote(str(a)) for a in self.argv)
        cwd_prefix = f"cd {shlex.quote(str(self.cwd))} && " if self.cwd and str(self.cwd) != "." else ""
        env_prefix = ""
        if self.env:
            env_prefix = " ".join(
                f"{k}={shlex.quote(str(v))}" for k, v in self.env.items()
            ) + " "
        self.debug(f"Launching: {cwd_prefix}{env_prefix}{cmd}")

        # Merge additional environment variables with current environment
        process_env = None
        if self.env:
            process_env = os.environ.copy()
            process_env.update(self.env)

        self.process = await asyncio.create_subprocess_exec(
            *wrap_bat_argv(self.argv),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd),
            env=process_env,
        )

        self.stdout_task = asyncio.create_task(
            self.__stream_handler(self.process.stdout, self.stdout_transform, "out"))
        self.stderr_task = asyncio.create_task(
            self.__stream_handler(self.process.stderr, self.stderr_transform, "err"))

    def failure(
        self,
        tool: str,
        message: str,
        log_path: Path | None = None,
    ) -> ToolFailure:
        """Build a ToolFailure describing this subprocess's non-zero exit.

        The returned exception is meant to be raised by the caller
        immediately. It captures argv, cwd, returncode, env overrides
        and the tail of the tool's raw output so the renderer can show
        a useful diagnostic without exposing the gbs traceback.
        """
        return ToolFailure(
            message=message,
            tool=tool,
            argv=self.argv,
            returncode=self.returncode,
            cwd=self.cwd,
            env_extra=self.env,
            log_tail=list(self.__output_tail),
            log_path=log_path,
        )

