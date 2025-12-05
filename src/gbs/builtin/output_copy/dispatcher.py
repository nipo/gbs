"""Output Copy Dispatcher

Copies files from the build fileset to the output paths specified
in the OutputGroup configuration.

Works backwards from unsatisfied outputs: finds outputs that have no
producer yet, locates matching source files in the fileset (by type),
and creates copy tasks.
"""

from __future__ import annotations
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext, BuildFileSet, BuildResource
from .task import CopyTask


class OutputCopyDispatcher(BaseDispatcher):
    """Dispatcher that copies build outputs to specified paths.

    This dispatcher runs late (high priority number) to ensure all
    other backends have generated their outputs. It works backwards
    from unsatisfied output goals:

    1. Find outputs marked is_output=True with no producer
    2. Look for source files in fileset matching the output's file_type
    3. Create copy task from source to output

    Priority: 900 (runs after compression and main compilation)
    """

    def __init__(self):
        super().__init__("output-copy", priority=900)

    def get_filter_variables(self):
        return {}

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Copy matching files to output paths.

        Works backwards: finds unsatisfied outputs, locates sources
        by file_type, creates copy tasks.

        Args:
            context: Build context
            fileset: BuildFileSet containing sources and output goals
        """
        # Get outputs that need producers
        unsatisfied = fileset.get_unsatisfied_outputs()

        for output_br in unsatisfied:
            file_type = output_br.file_type
            dest_path = output_br.path
            dest_resource = output_br.resource

            # Find source file with matching type (excluding the output itself)
            matching = [
                br for br in fileset.filter(file_type=file_type)
                if br.path != dest_path
            ]

            if not matching:
                self.logger.debug(
                    f"No source files of type '{file_type}' found "
                    f"for output '{dest_path}'"
                )
                continue

            if len(matching) > 1:
                self.logger.warning(
                    f"Multiple files of type '{file_type}' found, "
                    f"using first: {matching[0].path}"
                )

            source_br = matching[0]
            source_resource = source_br.resource

            # Skip if source and destination are the same
            if source_resource.path.resolve() == dest_path.resolve():
                self.logger.debug(
                    f"Source and destination are the same: {dest_path}, skip"
                )
                continue

            # Skip if already has a producer (shouldn't happen for unsatisfied)
            if dest_resource.depends_on:
                self.logger.debug(
                    f"Destination {dest_path} already has producer, skip"
                )
                continue

            self.logger.info(
                f"Copying {file_type}: {source_resource.path} -> {dest_path}"
            )

            # Create copy task
            CopyTask(
                context=context,
                source=source_resource,
                destination=dest_resource,
            )

            # Update the BuildResource to reflect it now has a producer
            output_br.generated_by = self.name
