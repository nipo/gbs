"""Windows implementations of platform abstractions."""

from __future__ import annotations
import asyncio
import ctypes
import ctypes.wintypes
import msvcrt
import os
import re
import subprocess

from ._process_info import ProcessInfo

# Regex to strip ANSI escape sequences from ConPTY output
_ANSI_ESCAPE_RE = re.compile(r'\x1b(?:\[[0-9;?]*[a-zA-Z]|\][^\x07]*\x07|\[[0-9]*[a-z])')



def wrap_bat_argv(argv: list[str]) -> list[str]:
    """Wrap .bat/.cmd files with cmd.exe /c for proper pipe inheritance.

    On Windows, running a .bat file directly via create_subprocess_exec
    can lose stdout/stderr pipes when the batch file chains into other
    processes via 'call'. Wrapping with 'cmd.exe /c' fixes this.
    """
    if argv and argv[0].lower().endswith(('.bat', '.cmd')):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c"] + argv
    return argv


class PtyProvider:
    """PTY operations on Windows using ConPTY via pywinpty.

    Provides pseudo-terminal support for tools like Vivado that
    buffer their output when connected to pipes instead of a terminal.
    """

    use_conpty = True

    try:
        from winpty import PtyProcess as _PtyProcess
        available = True
    except ImportError:
        _PtyProcess = None
        available = False

    @staticmethod
    def openpty():
        raise NotImplementedError(
            "Windows does not support openpty(). "
            "Use PtyProvider.spawn() instead."
        )

    @staticmethod
    async def connect_reader(loop, master_fd):
        raise NotImplementedError(
            "Windows does not support connect_reader(). "
            "Use PtyProvider.spawn() instead."
        )

    @staticmethod
    def spawn(argv: list[str], cwd: str, env: dict[str, str] | None = None) -> "ConPtySession":
        """Spawn a process in a ConPTY.

        Args:
            argv: Command and arguments
            cwd: Working directory
            env: Full environment dict (or None for inherited)

        Returns:
            ConPtySession wrapping the pywinpty process
        """
        if PtyProvider._PtyProcess is None:
            raise RuntimeError(
                "pywinpty is required for PTY support on Windows. "
                "Install it with: pip install pywinpty"
            )

        cmd_line = subprocess.list2cmdline(wrap_bat_argv(argv))
        pty_proc = PtyProvider._PtyProcess.spawn(cmd_line, cwd=cwd, env=env)
        return ConPtySession(pty_proc)


class ConPtySession:
    """Wraps a pywinpty PtyProcess for use by interactive.Session.

    Provides the same interface as the Unix PTY path:
    - An async-readable stdout stream
    - A stdin writer with write() and drain()
    - A pid property for process management
    - A cleanup method
    """

    def __init__(self, pty_proc):
        self._pty = pty_proc
        self._reader: asyncio.StreamReader | None = None
        self._feed_task: asyncio.Task | None = None

    @property
    def pid(self) -> int:
        return self._pty.pid

    def setup_async_reader(self) -> asyncio.StreamReader:
        """Create an asyncio StreamReader fed from the PTY.

        Must be called from within an async context.
        Returns a StreamReader that can be used like any async stream.
        """
        self._reader = asyncio.StreamReader()
        self._feed_task = asyncio.create_task(self._feed_reader())
        return self._reader

    async def _feed_reader(self):
        """Background task that reads from the PTY and feeds the StreamReader."""
        loop = asyncio.get_event_loop()
        try:
            while self._pty.isalive():
                try:
                    data = await loop.run_in_executor(None, self._pty.read, 4096)
                    if data:
                        # Strip ANSI escape sequences from ConPTY output
                        data = _ANSI_ESCAPE_RE.sub('', data)
                        if data:
                            self._reader.feed_data(data.encode('utf-8', errors='replace'))
                except EOFError:
                    break
                except Exception:
                    break
        finally:
            self._reader.feed_eof()

    @property
    def stdin_writer(self) -> "_ConPtyWriter":
        return _ConPtyWriter(self._pty)

    def close(self):
        """Clean up PTY resources."""
        if self._feed_task and not self._feed_task.done():
            self._feed_task.cancel()
        try:
            if self._pty.isalive():
                self._pty.terminate()
        except Exception:
            pass


class _ConPtyWriter:
    """Adapter giving pywinpty a write()/drain() interface.

    Converts \\n to \\r\\n because ConPTY is a terminal — it needs
    carriage return to submit commands, not bare line feed.
    """

    def __init__(self, pty_proc):
        self._pty = pty_proc

    def write(self, data: bytes):
        text = data.decode('utf-8', errors='replace')
        # Terminal needs \r\n, not bare \n
        text = text.replace('\n', '\r\n')
        self._pty.write(text)

    async def drain(self):
        pass


class ProcessControl:
    """Process management using Windows APIs."""

    @staticmethod
    def subprocess_extra_kwargs() -> dict:
        """Extra kwargs for asyncio.create_subprocess_exec to enable
        process tree isolation.

        On Windows, CREATE_NEW_PROCESS_GROUP gives us a separate
        group that can be targeted for termination.
        """
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    @staticmethod
    def kill_process_tree(pid: int, force: bool = False):
        """Kill a process and all its descendants.

        Uses 'taskkill /T /F' which terminates the process tree.
        On Windows there is no graceful/force distinction — taskkill /F
        is always used to ensure cleanup.

        Args:
            pid: Process ID
            force: Ignored on Windows (always forceful)
        """
        import subprocess as sp
        try:
            sp.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                stdout=sp.DEVNULL,
                stderr=sp.DEVNULL,
            )
        except FileNotFoundError:
            pass

    can_list_processes = False
    """Whether list_session_processes() is usable here."""

    @staticmethod
    def list_session_processes(sid: int) -> list[ProcessInfo]:
        """Not available on Windows: there is no session-id equivalent
        that survives the intermediate wrapper processes."""
        raise NotImplementedError(
            "Process listing is not supported on Windows"
        )

    @staticmethod
    def kill_process(pid: int, force: bool = False):
        """Kill a single process, leaving its descendants alone.

        Args:
            pid: Process ID
            force: Ignored on Windows (always forceful)
        """
        import subprocess as sp
        try:
            sp.run(
                ["taskkill", "/F", "/PID", str(pid)],
                stdout=sp.DEVNULL,
                stderr=sp.DEVNULL,
            )
        except FileNotFoundError:
            pass


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", ctypes.wintypes.DWORD),
        ("OffsetHigh", ctypes.wintypes.DWORD),
        ("hEvent", ctypes.wintypes.HANDLE),
    ]


class FileLockPrimitive:
    """Advisory whole-file locks using LockFileEx.

    Windows byte-range locks are mandatory: a locked range cannot be
    read or written through another handle. The lock therefore covers a
    single byte far past the end of the file, leaving the file content
    readable and writable by every holder and waiter. Windows drops the
    lock when the handle is closed, including on process death.
    """

    LOCKFILE_FAIL_IMMEDIATELY = 0x1
    LOCKFILE_EXCLUSIVE_LOCK = 0x2
    ERROR_LOCK_VIOLATION = 33
    REGION_OFFSET_HIGH = 0x40000000

    __kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    @classmethod
    def __region(cls) -> _Overlapped:
        overlapped = _Overlapped()
        overlapped.OffsetHigh = cls.REGION_OFFSET_HIGH
        return overlapped

    @classmethod
    def try_lock(cls, fd: int, exclusive: bool) -> bool:
        """Take the lock without blocking; return whether it was taken."""
        flags = cls.LOCKFILE_FAIL_IMMEDIATELY
        if exclusive:
            flags |= cls.LOCKFILE_EXCLUSIVE_LOCK
        overlapped = cls.__region()
        ok = cls.__kernel32.LockFileEx(
            ctypes.wintypes.HANDLE(msvcrt.get_osfhandle(fd)),
            flags, 0, 1, 0, ctypes.byref(overlapped),
        )
        if ok:
            return True
        error = ctypes.get_last_error()
        if error == cls.ERROR_LOCK_VIOLATION:
            return False
        raise ctypes.WinError(error)

    @classmethod
    def unlock(cls, fd: int) -> None:
        overlapped = cls.__region()
        ok = cls.__kernel32.UnlockFileEx(
            ctypes.wintypes.HANDLE(msvcrt.get_osfhandle(fd)),
            0, 1, 0, ctypes.byref(overlapped),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
