"""Output Copy Task

Copies a file from source to destination.
"""

from __future__ import annotations
import asyncio
import os
import shutil
import stat
from pathlib import Path

from ...build.task import Task, Resource
from ...build.context import BuildContext


def _copy_file(source_path: Path, dest_path: Path) -> None:
    """Synchronous file copy (runs in thread pool).

    Args:
        source_path: Source file path
        dest_path: Destination file path
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        os.chmod(dest_path, stat.S_IWRITE | stat.S_IREAD)
        dest_path.unlink()
    shutil.copy2(source_path, dest_path)
    # copy2 preserves source mode. A read-only source (e.g. a Gowin
    # bitstream) would leave a read-only destination that can't be
    # replaced on the next run, especially on Windows, so force it
    # writable -- but additively, so an execute bit (e.g. on the GHDL
    # simulator binary or its shell wrapper) survives the copy.
    mode = os.stat(dest_path).st_mode
    os.chmod(dest_path, mode | stat.S_IWRITE | stat.S_IREAD)


class CopyTask(Task):
    """Task that copies a file from source to destination

    Uses shutil.copy2 to preserve metadata, running in a thread pool
    to avoid blocking the event loop.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        source: Resource,
        destination: Resource,
    ):
        """Initialize copy task

        Args:
            context: Build context
            source: Source file resource
            destination: Destination file resource
        """
        super().__init__(
            dispatcher=dispatcher,
            name=f"copy:{destination.path.name}",
            inputs=[source],
            outputs=[destination],
            description=f"Copy {source.path.name} to {destination.path}",
        )

    async def work(self) -> None:
        """Execute the copy operation"""
        source, = self.inputs
        destination, = self.outputs
        self.info(f"Copying {source.path} -> {destination.path}")

        # Run copy in thread pool to avoid blocking event loop
        async with self.dispatcher.context.semaphore:
            await asyncio.to_thread(
                _copy_file,
                source.path,
                destination.path
            )

        self.info(f"Copied to {destination.path}")
