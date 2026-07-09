"""Compression Dispatcher

Compresses files in the pending work queue based on type suffixes.
For every unsatisfied output whose type ends with a known transform
(e.g. ``bitstream+gzip``), this dispatcher takes a matching base-type
producer already in the pending queue and emits a compressed
intermediate inside the build tree. Delivery to the user's requested
path is left to ``OutputCopyDispatcher``.

For chained transforms like ``+gzip+base64``, each dispatcher
iteration peels off the outermost transform; the next iteration sees
the produced intermediate as the base type for the following stage.
"""

from __future__ import annotations
from pathlib import Path

from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
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

    last_plus = type_str.rfind('+')
    base_type = type_str[:last_plus]
    transform = type_str[last_plus + 1:]
    return base_type, transform


class CompressDispatcher(BaseDispatcher):
    """Dispatcher that compresses files based on type suffixes.

    For each unsatisfied output whose type carries a known transform
    suffix, this dispatcher looks for a base-type producer in pending
    and emits a compressed intermediate in the build tree tagged with
    the same compressed type. OutputCopyDispatcher then copies that
    intermediate to the user's specified output path.

    The dispatcher does not touch the user's output resource, and
    does not write outside ``context.output_path``.
    """

    def __init__(self, context: BuildContext):
        super().__init__(context, "compress", tool_name="compress")

    async def process(self) -> None:
        """Emit compressed intermediates for unsatisfied compressed outputs.

        Runs once per dispatcher iteration; caller loops until pending
        stabilises. Idempotent: skips when a matching compressed
        intermediate is already in pending.
        """
        for dest_resource in self.context.get_pending_unsatisfied_outputs():
            base_type, transform = parse_output_type(dest_resource.file_type)

            if transform is None:
                # No transform suffix, not our job
                continue

            handler_info = COMPRESSION_HANDLERS.get(transform)
            if handler_info is None:
                # Unknown transform, another dispatcher may handle it
                continue

            task_class, extension = handler_info
            compressed_type = dest_resource.file_type

            self.debug(
                f"Output {compressed_type} needs transform '{transform}' "
                f"from base type '{base_type}'"
            )

            # If a producer intermediate for this compressed type
            # already exists in pending (from a previous iteration),
            # output-copy will handle the delivery.
            if self._has_producer(compressed_type):
                continue

            # A source of the base type is any pending resource whose
            # file_type matches, that already has a producer or is a
            # SOURCE/INTERMEDIATE (i.e. not an unsatisfied output goal).
            # openxc7 marks its terminal `.bit` as OUTPUT typology
            # because it treats it as its own final artifact, so we
            # cannot filter on typology alone — look for producers.
            producers = [
                r for r in self.context.filter_pending(file_type=base_type)
                if r.depends_on or r.typology != ResourceTypology.OUTPUT
            ]

            if not producers:
                # Wait for the base-type producer to appear on a later
                # dispatcher iteration.
                continue

            if len(producers) > 1:
                self.warning(
                    f"Multiple files of type '{base_type}' found, "
                    f"using first: {producers[0].path}"
                )

            source = producers[0]

            intermediate_path = self.context.output_path / (source.path.name + extension)
            intermediate = self.context.get_resource(
                intermediate_path,
                file_type=compressed_type,
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name,
            )
            self.context.add_pending(intermediate)

            self.info(
                f"Compressing {base_type} -> {compressed_type}: "
                f"{source.path} -> {intermediate.path}"
            )

            task_class(
                dispatcher=self,
                source=source,
                destination=intermediate,
            )

    def _has_producer(self, compressed_type: str) -> bool:
        """True when a resource of ``compressed_type`` is already
        queued with a producer attached (or is a plain intermediate),
        i.e. not an unsatisfied output goal.
        """
        for r in self.context.filter_pending(file_type=compressed_type):
            if r.typology == ResourceTypology.OUTPUT and not r.depends_on:
                continue
            return True
        return False
