"""Watchdog killing stalled helper processes of a tool session

Some tools spawn helper processes that occasionally hang forever and
prevent the tool from ever returning to its prompt. When such a helper is
not needed for a correct result, killing it unblocks the tool.
"""

from __future__ import annotations
import asyncio
from typing import Callable

from .. import logging
from .platform import ProcessControl, ProcessInfo

__all__ = ["ProcessWatchdog"]


class ProcessWatchdog:
    """Kills processes of a given name that outlive a grace period

    Scans the session of a root process and kills every descendant named
    `process_name` that has been alive for more than `grace_seconds`.
    Each killed process is reported once through `on_kill`.

    The callback runs in the watchdog task, so it must not block and must
    not take any lock the watched session holds while waiting for the
    process being killed.
    """

    def __init__(
        self,
        root_pid: int,
        process_name: str,
        grace_seconds: float,
        on_kill: Callable[[ProcessInfo], None],
        root_alive: Callable[[], bool] = lambda: True,
        interval: float | None = None,
        lister: Callable[[int], list[ProcessInfo]] = ProcessControl.list_session_processes,
        killer: Callable[..., None] = ProcessControl.kill_process,
    ):
        """Initialize watchdog

        Args:
            root_pid: Pid of the session leader whose descendants to watch
            process_name: Process name to match, as reported by the lister
            grace_seconds: Age above which a matching process is killed
            on_kill: Called with the ProcessInfo of each killed process
            root_alive: Returns False once the watched session is over
            interval: Seconds between scans, defaults to a quarter of the
                      grace period, capped at one second
            lister: Process lister, injectable for tests
            killer: Process killer, injectable for tests
        """
        self.root_pid = root_pid
        self.process_name = process_name
        self.grace_seconds = grace_seconds
        self.on_kill = on_kill
        self.root_alive = root_alive
        self.interval = interval if interval is not None else min(1.0, grace_seconds / 4)
        self.lister = lister
        self.killer = killer
        self.killed = set()
        self.task = None
        self.logger = logging.get_logger(self.__class__.__name__)

    def start(self) -> None:
        """Start the scanning task, if not already running"""
        if self.task is not None:
            return
        self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        """Stop the scanning task and wait for it to be gone"""
        task = self.task
        if task is None:
            return
        self.task = None
        task.cancel()
        # asyncio.wait() rather than awaiting the task directly: awaiting a
        # cancelled task raises CancelledError, which would be mistaken for
        # a cancellation of our own caller.
        await asyncio.wait({task})

    async def run(self) -> None:
        """Scan the session periodically until the root process is gone"""
        while self.root_alive():
            await asyncio.sleep(self.interval)

            # Liveness is not rechecked after the sleep: descendants outlive
            # the root process and keep its session id, so a stalled helper
            # orphaned by the root's exit still gets cleaned up.
            try:
                self.scan()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # A transient listing error must not silently end the watchdog
                self.logger.debug(f"Process scan failed: {e}")

    def scan(self) -> None:
        """Kill matching processes that are past the grace period"""
        for info in self.lister(self.root_pid):
            if info.name != self.process_name:
                continue
            if info.age < self.grace_seconds:
                continue
            if info.pid in (self.root_pid, 0, 1):
                continue

            key = (info.pid, info.starttime)
            if key in self.killed:
                continue

            # Recorded before the kill so a failing killer cannot make us
            # retry the same process on every scan.
            self.killed.add(key)
            self.killer(info.pid, force=True)
            self.on_kill(info)
