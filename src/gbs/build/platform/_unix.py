"""Unix implementations of platform abstractions."""

from __future__ import annotations
import asyncio
import os
import pty
import signal


def wrap_bat_argv(argv: list[str]) -> list[str]:
    """No-op on Unix — .bat wrapping is only needed on Windows."""
    return argv


class PtyProvider:
    """PTY operations using Unix pty module."""

    available = True
    use_conpty = False

    @staticmethod
    def openpty() -> tuple[int, int]:
        """Open a pseudo-terminal pair.

        Returns:
            (master_fd, slave_fd) tuple
        """
        return pty.openpty()

    @staticmethod
    async def connect_reader(loop, master_fd) -> tuple:
        """Connect an async reader to the PTY master fd.

        Returns:
            (transport, reader) tuple. Caller must close transport on cleanup.
        """
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        master_file = os.fdopen(master_fd, 'rb', buffering=0)
        transport, _ = await loop.connect_read_pipe(lambda: protocol, master_file)
        return transport, reader, master_file


class ProcessControl:
    """Process management using Unix signals and process groups."""

    @staticmethod
    def subprocess_extra_kwargs() -> dict:
        """Extra kwargs for asyncio.create_subprocess_exec to enable
        process group isolation."""
        return {"start_new_session": True}

    @staticmethod
    def kill_process_tree(pid: int, force: bool = False):
        """Kill a process and its entire process group.

        Args:
            pid: Process ID (must have been started with start_new_session=True)
            force: If True, use SIGKILL; otherwise SIGTERM
        """
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError):
            pass
