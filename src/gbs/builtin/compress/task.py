"""Compression Tasks

Task implementations for various compression formats.
"""

from __future__ import annotations
import asyncio
import gzip
import shutil
from pathlib import Path

from ...build.task import Task, Resource


def _gzip_file(source_path: Path, dest_path: Path) -> None:
    """Synchronous gzip compression (runs in thread pool).

    Args:
        source_path: Source file path
        dest_path: Destination file path
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(source_path, 'rb') as f_in:
        with gzip.open(dest_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)


class GzipTask(Task):
    """Task that compresses a file using gzip.

    Uses Python's gzip module for compression, running in a thread pool
    to avoid blocking the event loop.
    """

    def __init__(
        self,
        dispatcher,
        source: Resource,
        destination: Resource,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=f"gzip:{destination.path.name}",
            inputs=[source],
            outputs=[destination],
            description=f"Gzip {source.path.name}",
        )
        self.source = source
        self.destination = destination

    async def work(self) -> None:
        """Execute the gzip compression."""
        self.info(f"Compressing {self.source.path} -> {self.destination.path}")

        # Run compression in thread pool to avoid blocking event loop
        async with self.dispatcher.context.semaphore:
            await asyncio.to_thread(
                _gzip_file,
                self.source.path,
                self.destination.path
            )

        self.info(f"Compressed to {self.destination.path}")
