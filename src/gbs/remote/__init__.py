"""Remote execution support

Host-independent descriptions of what a remote gbs instance needs to
run part of a build: paths relative to a root table, resource and plan
segment descriptors, and content manifests backed by a blob store;
the framed messaging channel between two gbs instances, its handshake,
the hosts tools run on, planning through them, and running plan
segments on them.
"""

from .wire import WireError, WireFormat
from .roots import RootedPath, Root, RootTable
from .resource import ResourceMetadataCodec, ResourceDescriptor
from .manifest import ManifestEntry, ContentManifest, BlobStore
from .segment import PassDescriptor, OutputGroupDescriptor, SegmentDescriptor
from .channel import ChannelError, FrameError, ChannelClosed, Frame, FrameChannel
from .peer import RemoteError, MethodError, Reply, Event, Call, Peer
from .toolhost import ToolDescription, BackendUnavailable, ToolHost, LocalToolHost, RemoteToolHost
from .planning import PassContribution, RemotePass
from .handshake import (
    HandshakeError, SourceDigest, SourceFiles, Identity, PluginCompatibility, Hello, HelloReply,
)
from .transfer import BlobTransfer
from .segment_run import SegmentBuildContext, SegmentDispatchReply, SegmentRun
from .server import Workspace, RemoteServer, StdioChannel
from .client import RemoteHostError, RemoteHost
from .execution import (
    RemoteSegmentFailure, PlanSegment, PlanSegments, OutputInstaller,
    RemoteSegmentDispatcher, RemoteSegmentTask,
)

__all__ = [
    "WireError", "WireFormat",
    "RootedPath", "Root", "RootTable",
    "ResourceMetadataCodec", "ResourceDescriptor",
    "ManifestEntry", "ContentManifest", "BlobStore",
    "PassDescriptor", "OutputGroupDescriptor", "SegmentDescriptor",
    "ChannelError", "FrameError", "ChannelClosed", "Frame", "FrameChannel",
    "RemoteError", "MethodError", "Reply", "Event", "Call", "Peer",
    "ToolDescription", "BackendUnavailable", "ToolHost", "LocalToolHost", "RemoteToolHost",
    "PassContribution", "RemotePass",
    "HandshakeError", "SourceDigest", "SourceFiles", "Identity", "PluginCompatibility",
    "Hello", "HelloReply",
    "BlobTransfer",
    "SegmentBuildContext", "SegmentDispatchReply", "SegmentRun",
    "Workspace", "RemoteServer", "StdioChannel",
    "RemoteHostError", "RemoteHost",
    "RemoteSegmentFailure", "PlanSegment", "PlanSegments", "OutputInstaller",
    "RemoteSegmentDispatcher", "RemoteSegmentTask",
]
