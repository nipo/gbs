"""Output Copy Task

Copies a file from source to destination.
"""

from __future__ import annotations
import shutil

from ...build.task import Task, Resource
from ...build.context import BuildContext


class CopyTask(Task):
    """Task that copies a file from source to destination

    Uses shutil.copy2 to preserve metadata.
    """

    def __init__(
        self,
        context: BuildContext,
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
            context=context,
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

        # Ensure destination directory exists
        destination.path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure no file is at output
        destination.path.unlink(missing_ok = True)
        
        # Copy file with metadata preservation
        shutil.copy2(source.path, destination.path)

        self.logger.info(f"Copied to {destination.path}")
