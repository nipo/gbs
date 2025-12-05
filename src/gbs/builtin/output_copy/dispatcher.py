"""Output Copy Dispatcher

Copies files from the build fileset to the output paths specified
in the OutputGroup configuration.
"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext, BuildFileSet, BuildResource
from .task import CopyTask

class OutputCopyDispatcher(BaseDispatcher):
    """Dispatcher that copies build outputs to specified paths

    This dispatcher runs late (high priority number) to ensure all
    other backends have generated their outputs. It finds files in
    the fileset matching the requested output types and creates
    copy tasks to move them to the paths specified in OutputGroup.outputs.

    Priority: 900 (runs after main compilation)
    """

    def __init__(self):
        """Initialize output copy dispatcher
        """
        super().__init__("output-copy", priority=900)

    def get_filter_variables(self):
        return {}
        
    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Copy matching files to output paths

        Finds files in the fileset matching the requested output types
        and creates copy tasks to their destination paths.

        Args:
            context: Build context with output_group info
            fileset: BuildFileSet containing generated files
        """
        output_group = context.get_output_group()
        if output_group is None:
            self.logger.warning("No output group set in context, skipping output copy")
            return

        # Build a mapping from file_type to destination path
        type_to_path: dict[str, Path] = {}
        for output in output_group.outputs:
            type_to_path[output.type] = output.path.resolve()

        self.logger.debug(f"Output mappings: {type_to_path}")

        # Find files matching each output type
        for file_type, dest_path in type_to_path.items():
            # Create destination resource
            dest_resource = context.get_resource(dest_path, metadata = {
                "file_type": file_type,
            })

            if dest_resource.depends_on:
                self.logger.debug(
                    f"Destination {dest_path} has creator, skip"
                )
                continue

            matching = fileset.filter(file_type=file_type)

            if not matching:
                self.logger.warning(
                    f"No files of type '{file_type}' found in fileset "
                    f"for output path '{dest_path}'"
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
            self.logger.debug(
                f"Will copy {source_resource.path} to {dest_resource.path}"
            )

            # Create copy task
            CopyTask(
                context=context,
                source=source_resource,
                destination=dest_resource,
            )

            # Add destination to fileset
            dest_br = BuildResource(
                resource=dest_resource,
                file_type=file_type,
                library=source_br.library,
                is_source=False,
                generated_by=self.name,
            )
            fileset.add(dest_br)
