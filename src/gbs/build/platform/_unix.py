"""Unix implementations of platform abstractions."""

from __future__ import annotations
import asyncio
import os
import pty
import signal
import sys

from ._process_info import ProcessInfo


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

    can_list_processes = sys.platform.startswith("linux")
    """Whether list_session_processes() is usable here (it needs /proc)."""

    @staticmethod
    def stat_parse(line: str, uptime: float, clk_tck: int) -> ProcessInfo | None:
        """Parse one /proc/<pid>/stat line.

        Args:
            line: Raw contents of /proc/<pid>/stat
            uptime: Seconds since boot, from /proc/uptime
            clk_tck: Clock ticks per second, from os.sysconf("SC_CLK_TCK")

        Returns:
            ProcessInfo, or None when the line does not parse: a process
            can exit mid-read and leave a truncated line behind.
        """
        opening = line.find("(")
        closing = line.rfind(")")
        if opening < 0 or closing < opening:
            return None

        try:
            pid = int(line[:opening].split()[0])
            name = line[opening + 1:closing]
            # comm is the only field that may contain spaces or parentheses,
            # so field indices are stable only past its closing paren.
            fields = line[closing + 2:].split()
            state = fields[0]
            session = int(fields[3])
            starttime = int(fields[19])
        except (ValueError, IndexError):
            return None

        return ProcessInfo(
            pid=pid,
            name=name,
            session=session,
            state=state,
            age=uptime - starttime / clk_tck,
            starttime=starttime,
        )

    @staticmethod
    def list_session_processes(sid: int) -> list[ProcessInfo]:
        """List live processes belonging to session `sid`.

        Session id is a robust ownership criterion: a process launched with
        start_new_session=True is a session leader whose session id equals
        its pid, and every descendant inherits it unless it calls setsid().

        Args:
            sid: Session id to match

        Raises:
            NotImplementedError: On platforms without /proc
        """
        if not ProcessControl.can_list_processes:
            raise NotImplementedError(
                f"Process listing is not supported on {sys.platform}"
            )

        clk_tck = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime") as f:
            uptime = float(f.readline().split()[0])

        result = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue

            try:
                with open(f"/proc/{entry}/stat") as f:
                    line = f.readline()
            except OSError:
                # Exited between listdir and open, or hidepid mount
                continue

            info = ProcessControl.stat_parse(line, uptime, clk_tck)
            if info is None:
                continue
            # A killed process stays a zombie until its parent reaps it;
            # reporting it would have the caller kill it over and over.
            if info.state in ("Z", "X", "x"):
                continue
            if info.session != sid:
                continue

            result.append(info)

        return result

    @staticmethod
    def kill_process(pid: int, force: bool = False):
        """Kill a single process, leaving the rest of its group alone.

        Args:
            pid: Process ID
            force: If True, use SIGKILL; otherwise SIGTERM
        """
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
