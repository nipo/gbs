"""Compression Dispatcher

Compresses files in the build fileset based on type suffixes.
Works backwards from unsatisfied outputs: if an output needs type
"ise-bitstream+gzip", this dispatcher creates a compressed version
from "ise-bitstream" source.

The dispatcher handles one transform at a time. For chained transforms
like "+gzip+base64", each dispatcher iteration handles the outermost
transform, creating intermediate goals for inner transforms.
"""

from __future__ import annotations
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext, BuildFileSet, BuildResource
from .task import GzipTask


# Mapping from compression suffix to (task_class, file_extension)
COMPRESSION_HANDLERS = {
    "gzip": (GzipTask, ".gz"),
}


def parse_output_type(type_str: str) -> tuple[str, str | None]:
    """Parse an output type string to extract the last transform.

    Args:
        type_str: Type string like "ise-bitstream+gzip" or "gowin-fs+gzip+base64"

    Returns:
        Tuple of (base_type_without_last_transform, last_transform or None)

    Examples:
        >>> parse_output_type("ise-bitstream")
        ("ise-bitstream", None)
        >>> parse_output_type("ise-bitstream+gzip")
        ("ise-bitstream", "gzip")
        >>> parse_output_type("gowin-fs+gzip+base64")
        ("gowin-fs+gzip", "base64")
    """
    if '+' not in type_str:
        return type_str, None

    # Split off the last transform
    last_plus = type_str.rfind('+')
    base_type = type_str[:last_plus]
    transform = type_str[last_plus + 1:]
    return base_type, transform


class CompressDispatcher(BaseDispatcher):
    """Dispatcher that compresses files based on type suffixes.

    This dispatcher runs before output-copy (priority 850) to ensure
    compressed variants are available for copying.

    It works backwards from unsatisfied outputs:
    1. Find outputs with compression suffixes (e.g., "+gzip") that have no producer
    2. Strip the last transform to get the source type needed
    3. Look for source in fileset, or create an intermediate output goal
    4. Create compression task from source to output

    Priority: 850 (runs after main compilation, before output-copy)
    """

    def __init__(self):
        super().__init__("compress", priority=850)

    def get_filter_variables(self):
        return {}

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Create compression tasks for unsatisfied compressed outputs.

        Works backwards: finds outputs needing compression, locates or
        creates source, then creates compression task.

        Args:
            context: Build context
            fileset: BuildFileSet containing sources and output goals
        """
        # Get outputs that need producers
        unsatisfied = fileset.get_unsatisfied_outputs()

        for output_br in unsatisfied:
            base_type, transform = parse_output_type(output_br.file_type)

            if transform is None:
                # No transform suffix, not our job
                continue

            handler_info = COMPRESSION_HANDLERS.get(transform)
            if handler_info is None:
                # Unknown transform, skip (might be handled by another dispatcher)
                continue

            task_class, extension = handler_info

            self.logger.debug(
                f"Output {output_br.file_type} needs transform '{transform}' "
                f"from base type '{base_type}'"
            )

            # Look for source file with base type
            matching = fileset.filter(file_type=base_type)

            if not matching:
                # Source doesn't exist yet - create an intermediate output goal
                # This will be satisfied by another dispatcher (or previous iteration)
                intermediate_path = context.output_path / self._generate_intermediate_name(
                    output_br.path, extension
                )
                intermediate_resource = context.get_resource(intermediate_path, metadata={
                    "file_type": base_type,
                })

                # Only create if not already in fileset
                if intermediate_path not in fileset:
                    intermediate_br = BuildResource(
                        resource=intermediate_resource,
                        file_type=base_type,
                        is_source=False,
                        is_output=True,  # Mark as goal so other dispatchers can satisfy it
                        generated_by=None,
                    )
                    fileset.add(intermediate_br)
                    self.logger.info(
                        f"Created intermediate goal: {base_type} at {intermediate_path}"
                    )
                continue

            if len(matching) > 1:
                self.logger.warning(
                    f"Multiple files of type '{base_type}' found, "
                    f"using first: {matching[0].path}"
                )

            source_br = matching[0]
            source_resource = source_br.resource
            dest_resource = output_br.resource

            # Skip if already has a producer (shouldn't happen for unsatisfied, but be safe)
            if dest_resource.depends_on:
                self.logger.debug(f"Output {output_br.path} already has producer, skip")
                continue

            self.logger.info(
                f"Compressing {base_type} -> {output_br.file_type}: "
                f"{source_resource.path} -> {dest_resource.path}"
            )

            # Create compression task
            task_class(
                context=context,
                source=source_resource,
                destination=dest_resource,
            )

            # Update the BuildResource to reflect it now has a producer
            output_br.generated_by = self.name

    def _generate_intermediate_name(self, output_path: Path, extension: str) -> str:
        """Generate name for intermediate file by removing extension.

        Args:
            output_path: Final output path (e.g., firmware.bit.gz)
            extension: Extension added by this transform (e.g., .gz)

        Returns:
            Intermediate filename (e.g., firmware.bit)
        """
        name = output_path.name
        if name.endswith(extension):
            return name[:-len(extension)]
        # Fallback: just use the name without last extension
        return output_path.stem
