"""Compression Tasks

Task implementations for various compression formats.
"""

from __future__ import annotations
import gzip
import shutil

from ...build.task import Task, Resource
from ...build.context import BuildContext


class GzipTask(Task):
    """Task that compresses a file using gzip.

    Uses Python's gzip module for compression.
    """

    def __init__(
        self,
        context: BuildContext,
        source: Resource,
        destination: Resource,
    ):
        """Initialize gzip task.

        Args:
            context: Build context
            source: Source file resource
            destination: Destination file resource (compressed)
        """
        super().__init__(
            context=context,
            name=f"gzip:{destination.path.name}",
            inputs=[source],
            outputs=[destination],
            description=f"Gzip {source.path.name}",
        )
        self.source = source
        self.destination = destination

    async def work(self) -> None:
        """Execute the gzip compression."""
        self.logger.info(f"Compressing {self.source.path} -> {self.destination.path}")

        # Ensure destination directory exists
        self.destination.path.parent.mkdir(parents=True, exist_ok=True)

        # Compress file
        async with self.context.semaphore:
            with open(self.source.path, 'rb') as f_in:
                with gzip.open(self.destination.path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

        self.logger.info(f"Compressed to {self.destination.path}")
