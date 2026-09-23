"""Tests for the inter-process file lock"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gbs.build.context import BuildContext
from gbs.build.lock import FileLock
from gbs.utils import clean_paths


HOLDER_SCRIPT = """
import asyncio, sys
from pathlib import Path
from gbs.build.lock import FileLock

async def main():
    lock = FileLock(Path(sys.argv[1]), exclusive=sys.argv[2] == "exclusive")
    await lock.acquire()
    print("locked", flush=True)
    sys.stdin.readline()
    lock.release()

asyncio.run(main())
"""


class Holder:
    """Child process holding a lock until told to release it."""

    def __init__(self, path, exclusive):
        self.process = subprocess.Popen(
            [sys.executable, "-c", HOLDER_SCRIPT, str(path),
             "exclusive" if exclusive else "shared"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

    @property
    def pid(self):
        return self.process.pid

    def wait_locked(self):
        assert self.process.stdout.readline().strip() == "locked"

    def release(self):
        self.process.stdin.write("\n")
        self.process.stdin.flush()
        assert self.process.wait(timeout=10) == 0

    def kill(self):
        self.process.kill()
        self.process.wait(timeout=10)


class Reporter:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "sub" / ".lock"


@pytest.fixture
def holders():
    started = []

    def start(path, exclusive, wait_locked=True):
        holder = Holder(path, exclusive)
        started.append(holder)
        if wait_locked:
            holder.wait_locked()
        return holder

    yield start
    for holder in started:
        if holder.process.poll() is None:
            holder.kill()


async def acquire_blocks(lock, delay=0.5):
    """Start acquiring, check it is still pending after delay."""
    task = asyncio.create_task(lock.acquire())
    await asyncio.sleep(delay)
    assert not task.done()
    return task


async def test_exclusive_blocks_exclusive(lock_path, holders):
    holder = holders(lock_path, exclusive=True)
    reporter = Reporter()
    lock = FileLock(lock_path, exclusive=True, reporter=reporter)

    task = await acquire_blocks(lock)
    assert len(reporter.warnings) == 1
    assert f"held by pid {holder.pid}" in reporter.warnings[0]

    await asyncio.to_thread(holder.release)
    await asyncio.wait_for(task, timeout=5)
    assert lock.held
    lock.release()
    assert not lock.held


async def test_exclusive_blocks_shared(lock_path, holders):
    holder = holders(lock_path, exclusive=True)
    lock = FileLock(lock_path, exclusive=False, reporter=Reporter())

    task = await acquire_blocks(lock)
    await asyncio.to_thread(holder.release)
    await asyncio.wait_for(task, timeout=5)
    lock.release()


async def test_shared_allows_shared(lock_path, holders):
    holder = holders(lock_path, exclusive=False)
    reporter = Reporter()

    async with FileLock(lock_path, exclusive=False, reporter=reporter) as lock:
        assert lock.held
        # A second holder process joins while both hold the lock.
        other = holders(lock_path, exclusive=False)
        await asyncio.to_thread(other.release)

    assert reporter.warnings == []
    await asyncio.to_thread(holder.release)


async def test_shared_blocks_exclusive(lock_path, holders):
    holder = holders(lock_path, exclusive=False)
    reporter = Reporter()
    lock = FileLock(lock_path, exclusive=True, reporter=reporter)

    task = await acquire_blocks(lock)
    assert len(reporter.warnings) == 1
    await asyncio.to_thread(holder.release)
    await asyncio.wait_for(task, timeout=5)
    lock.release()


async def test_held_exclusive_blocks_other_process(lock_path, holders):
    async with FileLock(lock_path, exclusive=True):
        holder = holders(lock_path, exclusive=False, wait_locked=False)
        await asyncio.sleep(0.5)
        assert holder.process.poll() is None
    await asyncio.to_thread(holder.wait_locked)
    await asyncio.to_thread(holder.release)


async def test_holder_death_releases(lock_path, holders):
    holder = holders(lock_path, exclusive=True)
    lock = FileLock(lock_path, exclusive=True, reporter=Reporter())

    task = await acquire_blocks(lock)
    holder.kill()
    await asyncio.wait_for(task, timeout=5)
    lock.release()


async def test_cancelled_waiter_holds_nothing(lock_path, holders):
    holder = holders(lock_path, exclusive=True)
    lock = FileLock(lock_path, exclusive=True, reporter=Reporter())

    task = await acquire_blocks(lock)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.held

    await asyncio.to_thread(holder.release)
    other = holders(lock_path, exclusive=True)
    await asyncio.to_thread(other.release)


@pytest.mark.skipif(sys.platform == "win32",
                    reason="A lock file in use cannot be deleted on Windows")
async def test_lock_file_replaced_while_waiting(lock_path, holders):
    holder = holders(lock_path, exclusive=True)
    lock = FileLock(lock_path, exclusive=True, reporter=Reporter())

    task = await acquire_blocks(lock)
    # The file the waiter is blocked on disappears; a new run then
    # takes the lock on the file now at the path.
    os.unlink(lock_path)
    newcomer = holders(lock_path, exclusive=True)
    await asyncio.to_thread(holder.release)
    await asyncio.sleep(0.5)
    assert not task.done()

    await asyncio.to_thread(newcomer.release)
    await asyncio.wait_for(task, timeout=5)
    lock.release()


async def test_double_acquire_rejected(lock_path):
    lock = FileLock(lock_path)
    async with lock:
        with pytest.raises(RuntimeError):
            await lock.acquire()
    with pytest.raises(RuntimeError):
        lock.release()


async def test_clean_under_tree_locks_keeps_lock_files(tmp_path, monkeypatch, holders):
    monkeypatch.chdir(tmp_path)
    ctx = BuildContext(base_output_path=Path("gbs-build"))
    ctx.output_path = Path("gbs-build") / "group"
    for guarded in (ctx.output_path, ctx.shared_cache_root):
        (guarded / "sub").mkdir(parents=True)
        (guarded / "sub" / "artifact").write_text("x")
    group_lock = Path("gbs-build") / "group.lock"

    async with ctx.tree_locks(cache_exclusive=True):
        other = holders(tmp_path / group_lock, exclusive=True, wait_locked=False)
        clean_paths({ctx.output_path, ctx.shared_cache_root})
        assert not ctx.output_path.exists()
        assert not ctx.shared_cache_root.exists()
        assert group_lock.exists()
        assert (Path("gbs-build") / "cache.lock").exists()
        locked = asyncio.create_task(asyncio.to_thread(other.wait_locked))
        await asyncio.sleep(0.5)
        assert not locked.done()

    await asyncio.wait_for(locked, timeout=5)
    await asyncio.to_thread(other.release)
