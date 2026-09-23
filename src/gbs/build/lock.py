"""Advisory inter-process file locks for concurrent gbs runs.

Several gbs processes may work on the same build tree at once (for
example two output groups built from the same directory in two
terminals). Locks serialize the parts of the tree they would otherwise
corrupt: an output group directory, a shared cache entry being
published, or a whole shared cache being wiped.
"""

from __future__ import annotations
import asyncio
import os
from pathlib import Path
from typing import Optional

from ..logging import get_logger
from .platform import FileLockPrimitive

logger = get_logger(__name__)

__all__ = ["FileLock", "LockSet"]


class FileLock:
    """Shared or exclusive advisory lock on a file, usable from asyncio.

    The lock file is created on demand and never removed. Waiting does
    not block the event loop: the lock is polled with non-blocking
    attempts, so a cancelled waiter leaves nothing behind. The first
    failed attempt is reported through the reporter so the user knows
    why the run is stalled.

    Holders write their PID into the lock file. It only serves the
    waiting message; the lock itself is the OS lock on the open file,
    which the OS drops when its holder exits, so a crashed holder never
    leaves a stale lock.

    A lock file sits beside the file or directory it guards, never
    inside it, so wiping the guarded directory under the lock leaves
    the lock file alone. gbs never deletes lock files; on Windows, a
    lock file cannot even be deleted while any run has it open.

    Should the lock file still be unlinked while a waiter is blocked on
    it, the waiter would end up holding a lock nobody else can see.
    After acquisition the open file is therefore checked to still be
    the one at the path, and the acquisition is retried on the new file
    otherwise.
    """

    POLL_INTERVAL = 0.1
    PID_FIELD_SIZE = 16

    def __init__(self, path: Path, exclusive: bool = True, reporter=None):
        """
        Args:
            path: Lock file path. Parent directories are created.
            exclusive: Exclusive lock if True, shared lock otherwise.
            reporter: Object with a ``warning(str)`` method that receives
                the waiting message. Defaults to this module's logger.
        """
        self.path = path
        self.exclusive = exclusive
        self.reporter = reporter or logger
        self.__fd: Optional[int] = None

    @classmethod
    def beside(cls, guarded: Path, exclusive: bool = True, reporter=None) -> FileLock:
        """Lock guarding a file or directory, stored as its ``.lock`` sibling."""
        return cls(guarded.with_name(guarded.name + ".lock"), exclusive, reporter)

    @property
    def mode(self) -> str:
        return "exclusive" if self.exclusive else "shared"

    @property
    def held(self) -> bool:
        return self.__fd is not None

    async def acquire(self) -> None:
        if self.__fd is not None:
            raise RuntimeError(f"Lock {self.path} already held")

        waiting_reported = False
        while True:
            fd = self.__open()
            try:
                while not FileLockPrimitive.try_lock(fd, self.exclusive):
                    if not waiting_reported:
                        self.reporter.warning(self.__waiting_message(fd))
                        waiting_reported = True
                    await asyncio.sleep(self.POLL_INTERVAL)
                if self.__is_current(fd):
                    break
            except BaseException:
                os.close(fd)
                raise
            os.close(fd)

        self.__fd = fd
        self.__pid_write(fd)

    def release(self) -> None:
        if self.__fd is None:
            raise RuntimeError(f"Lock {self.path} not held")
        fd, self.__fd = self.__fd, None
        try:
            FileLockPrimitive.unlock(fd)
        finally:
            os.close(fd)

    async def __aenter__(self) -> FileLock:
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release()

    def __open(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(self.path, os.O_RDWR | os.O_CREAT, 0o666)

    def __is_current(self, fd: int) -> bool:
        try:
            on_path = os.stat(self.path)
        except FileNotFoundError:
            return False
        return os.path.samestat(os.fstat(fd), on_path)

    def __pid_write(self, fd: int) -> None:
        field = f"{os.getpid()}\n".encode().ljust(self.PID_FIELD_SIZE, b" ")
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, field)

    def __waiting_message(self, fd: int) -> str:
        os.lseek(fd, 0, os.SEEK_SET)
        content = os.read(fd, self.PID_FIELD_SIZE).decode(errors="replace").strip()
        holder = f" held by pid {content}" if content.isdigit() else ""
        return f"Waiting for {self.mode} lock on {self.path}{holder}"


class LockSet:
    """Several FileLocks acquired in order and released in reverse.

    Every user of a given set of lock files must acquire them in the
    same order to rule out deadlocks between processes.
    """

    def __init__(self, locks: list[FileLock]):
        self.locks = locks

    async def acquire(self) -> None:
        try:
            for lock in self.locks:
                await lock.acquire()
        except BaseException:
            self.release()
            raise

    def release(self) -> None:
        for lock in reversed(self.locks):
            if lock.held:
                lock.release()

    async def __aenter__(self) -> LockSet:
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release()
