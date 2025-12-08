"""Output Copy Task

Copies a file from source to destination.
"""

from __future__ import annotations
import asyncio
import shutil
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
    dest_path.unlink(missing_ok=True)
    shutil.copy2(source_path, dest_path)


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
        self.logger.info(f"Copying {source.path} -> {destination.path}")

        # Run copy in thread pool to avoid blocking event loop
        async with self.dispatcher.context.semaphore:
            await asyncio.to_thread(
                _copy_file,
                source.path,
                destination.path
            )

        self.logger.info(f"Copied to {destination.path}")
