"""Output Copy Dispatcher

Copies files from the pending work queue to the output paths specified
in the OutputGroup configuration.

Works backwards from unsatisfied outputs: finds outputs that have no
producer yet, locates matching source files in the pending queue (by type),
and creates copy tasks.
"""

from __future__ import annotations
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext
from .task import CopyTask


class OutputCopyDispatcher(BaseDispatcher):
    """Dispatcher that copies build outputs to specified paths.

    This dispatcher runs late (high priority number) to ensure all
    other backends have generated their outputs. It works backwards
    from unsatisfied output goals:

    1. Find outputs marked typology=OUTPUT with no producer
    2. Look for source files in pending queue matching the output's file_type
    3. Create copy task from source to output

    Priority: 900 (runs after compression and main compilation)
    """

    def __init__(self, context: BuildContext):
        super().__init__(context, "output-copy", tool_name="output-copy", priority=900)

    async def process(self) -> None:
        """Copy matching files to output paths.

        Works backwards: finds unsatisfied outputs, locates sources
        by file_type, creates copy tasks.

        Uses self.context to access the build context.
        """
        # Get outputs that need producers
        unsatisfied = self.context.get_pending_unsatisfied_outputs()

        for dest_resource in unsatisfied:
            file_type = dest_resource.file_type
            dest_path = dest_resource.path

            # Find source file with matching type (excluding the output itself)
            matching = [
                res for res in self.context.filter_pending(file_type=file_type)
                if res.path != dest_path
            ]

            if not matching:
                self.debug(
                    f"No source files of type '{file_type}' found "
                    f"for output '{dest_path}'"
                )
                continue

            if len(matching) > 1:
                self.warning(
                    f"Multiple files of type '{file_type}' found, "
                    f"using first: {matching[0].path}"
                )

            source_resource = matching[0]

            # Skip if source and destination are the same
            if source_resource.path.resolve() == dest_path.resolve():
                self.debug(
                    f"Source and destination are the same: {dest_path}, skip"
                )
                continue

            # Skip if already has a producer (shouldn't happen for unsatisfied)
            if dest_resource.depends_on:
                self.debug(
                    f"Destination {dest_path} already has producer, skip"
                )
                continue

            self.info(
                f"Copying {file_type}: {source_resource.path} -> {dest_path}"
            )

            # Create copy task
            CopyTask(
                dispatcher=self,
                source=source_resource,
                destination=dest_resource,
            )

            # Update the resource to reflect it now has a producer
            dest_resource.generated_by = self.name

            # Track output paths for cleaning
            if not hasattr(self, '_output_paths'):
                self._output_paths = set()
            self._output_paths.add(dest_path)

    def get_clean_paths(self) -> set:
        """Return paths that should be cleaned by this dispatcher

        Includes both the working directory and any output files we copied.

        Returns:
            Set of Path objects to clean
        """
        paths = {self.context.output_path}

        # Add any output files we copied
        if hasattr(self, '_output_paths'):
            paths |= self._output_paths

        return paths
