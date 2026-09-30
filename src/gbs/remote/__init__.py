"""Remote execution support

Host-independent descriptions of what a remote gbs instance needs to
run part of a build: paths relative to a root table, resource and plan
segment descriptors, and content manifests backed by a blob store.
"""

from .wire import WireError, WireFormat
from .roots import RootedPath, Root, RootTable
from .resource import ResourceMetadataCodec, ResourceDescriptor
from .manifest import ManifestEntry, ContentManifest, BlobStore
from .segment import PassDescriptor, OutputGroupDescriptor, SegmentDescriptor

__all__ = [
    "WireError", "WireFormat",
    "RootedPath", "Root", "RootTable",
    "ResourceMetadataCodec", "ResourceDescriptor",
    "ManifestEntry", "ContentManifest", "BlobStore",
    "PassDescriptor", "OutputGroupDescriptor", "SegmentDescriptor",
]
