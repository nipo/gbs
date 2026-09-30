"""Running a plan segment for another gbs instance

The serving side of remote segment execution. A segment is dispatched
first, while the client plans: the passes are recreated from this
host's backends and dispatched in a build context of their own, over
the segment's roots placed in a directory of the workspace, which
tells the client which resources the segment produces. It is executed
later, once the client has its inputs: they are materialized, the
build runs, and its outputs are stored in the blob store for the
client to fetch.
"""

from __future__ import annotations
import asyncio
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from ..build.context import BuildContext
from ..build.task import ResourceTypology, Task
from ..logging import get_logger
from ..plugins import get_plugin_registry
from ..ui.messages import ToolMessage
from .handshake import PluginCompatibility
from .manifest import BlobStore, ContentManifest, ManifestEntry
from .peer import Call, MethodError
from .resource import ResourceDescriptor
from .roots import RootTable
from .segment import SegmentDescriptor
from .wire import WireError, WireObject

__all__ = ["SegmentBuildContext", "SegmentDispatchReply", "SegmentRun"]

logger = get_logger(__name__)


class SegmentBuildContext(BuildContext):
    """Build context reporting tool messages and step progress to a sink

    Args:
        sink: Called with (event name, JSON data) for each report
        table: Root table host paths in messages are expressed against
    """

    def __init__(self, sink: Callable[[str, Any], None], table: RootTable, **kwargs):
        super().__init__(**kwargs)
        self.sink = sink
        self.table = table

    def message_add(self, message: ToolMessage):
        super().message_add(message)
        self.sink("message", self.message_encode(message))

    def message_encode(self, message: ToolMessage) -> dict[str, Any]:
        location: Any = None
        if message.file_path is not None:
            path = Path(message.file_path)
            if path.is_absolute() and self.table.root_of(path) is not None:
                location = self.table.locate(path).to_json()
            else:
                location = str(path)
        origin = message.origin
        return {
            "severity": message.severity.value,
            "message": message.message,
            "identifier": None if message.identifier is None else str(message.identifier),
            "extended_message": message.extended_message,
            "file": location,
            "line": message.line,
            "column": message.column,
            "step": None if origin is None else origin.pretty_name,
        }

    def _on_step_start(self, step):
        super()._on_step_start(step)
        self.progress_report(step)

    def _on_step_complete(self, step):
        super()._on_step_complete(step)
        self.progress_report(step)

    def progress_report(self, step) -> None:
        if not step._EMIT_UI_PROGRESS:
            return
        self.sink("progress", {
            "completed": self._completed_steps,
            "total": self._total_steps,
            "step": step.pretty_name,
        })


class SegmentDispatchReply:
    """What `segment.dispatch` answers

    Attributes:
        id: Segment id, for `segment.execute`
        goals: The goals, as the segment produces them
        exported: Resources the segment produces for other segments
        pending_inputs: Indices of the inputs the segment's dispatchers
            left in the pending queue, which the client should leave
            to other dispatchers too
    """

    def __init__(self, id: int, goals: list[ResourceDescriptor],
                 exported: list[ResourceDescriptor], pending_inputs: list[int]):
        self.id = id
        self.goals = goals
        self.exported = exported
        self.pending_inputs = pending_inputs

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goals": [d.to_json() for d in self.goals],
            "exported": [d.to_json() for d in self.exported],
            "pending_inputs": list(self.pending_inputs),
        }

    @classmethod
    def from_json(cls, data: Any) -> SegmentDispatchReply:
        reader = WireObject(data, "segment.dispatch result")
        id = reader.field("id", int)
        goals = [ResourceDescriptor.from_json(d) for d in reader.field("goals", list)]
        exported = [ResourceDescriptor.from_json(d) for d in reader.field("exported", list)]
        pending_inputs = reader.field("pending_inputs", list)
        reader.finish()
        for index in pending_inputs:
            if not isinstance(index, int) or isinstance(index, bool):
                raise WireError("segment.dispatch result: pending_inputs must hold integers")
        return cls(id, goals, exported, pending_inputs)


class SegmentRun:
    """One segment, from its dispatch to its execution

    Attributes:
        id: Segment id on this connection
        directory: Where the segment's roots are placed
        descriptor: What the client sent
        compatibility: Plugins usable with the client
        table: Root table placed in directory
        context: Build context of the segment, once dispatched
        outputs: Goals and exported resources, once dispatched
    """

    def __init__(self, id: int, directory: Path, descriptor: SegmentDescriptor,
                 compatibility: PluginCompatibility,
                 gbs_config: Any, blob_store: BlobStore, semaphore: asyncio.Semaphore,
                 keep: bool):
        self.id = id
        self.directory = directory
        self.descriptor = descriptor
        self.compatibility = compatibility
        self.gbs_config = gbs_config
        self.blob_store = blob_store
        self.semaphore = semaphore
        self.keep = keep
        self.table = descriptor.roots.placed(directory)
        self.context: Optional[SegmentBuildContext] = None
        self.outputs: list[ResourceDescriptor] = []
        self.materialized = ContentManifest([])
        self.executed = False
        self.__call: Optional[Call] = None
        self.__events: Optional[asyncio.Queue] = None

    def event_send(self, name: str, data: Any) -> None:
        """Queue an event for the request being served, if any"""
        if self.__events is not None:
            self.__events.put_nowait((name, data))

    async def __events_forward(self) -> None:
        """Send queued events until the None that ends them"""
        while (item := await self.__events.get()) is not None:
            name, data = item
            await self.__call.event(name, data)

    def passes_check(self) -> None:
        """Refuse passes of plugins incompatible with the client

        Raises:
            MethodError: IncompatiblePlugin
        """
        registry = get_plugin_registry()
        problems = []
        for p in self.descriptor.passes:
            plugin = registry.backend_plugin(p.backend)
            if plugin is None:
                problems.append(f"pass {p.name}: backend {p.backend} is not installed here")
                continue
            problem = self.compatibility.problem(plugin)
            if problem is not None:
                problems.append(f"pass {p.name}: {problem}")
        if problems:
            raise MethodError("IncompatiblePlugin", "; ".join(problems))

    def generic_check(self, generic: dict[str, list]) -> None:
        """Refuse generic dispatchers differing from the client's

        The plugins whose generic dispatchers are registered here must
        be those the client registers, and be compatible with it.

        Args:
            generic: Generic dispatchers of this host, by plugin name

        Raises:
            MethodError: IncompatiblePlugin
        """
        expected = self.descriptor.generic_plugins
        problems = []
        for plugin in sorted(set(generic) | expected):
            if plugin not in expected:
                names = ", ".join(d.name for d in generic[plugin])
                reason = self.compatibility.problem(plugin) or f"plugin {plugin} is not active on client"
                problems.append(f"{reason}, but has generic dispatchers here; "
                                f"exclude them with exclude_dispatchers: [{names}]")
            elif plugin not in generic:
                reason = self.compatibility.problem(plugin) or f"plugin {plugin} is not active here"
                problems.append(f"{reason}, but client registers its generic dispatchers")
            else:
                reason = self.compatibility.problem(plugin)
                if reason is not None:
                    problems.append(f"{reason}; its generic dispatchers are registered on both hosts")
        if problems:
            raise MethodError("IncompatiblePlugin", "; ".join(problems))

    async def dispatch(self) -> SegmentDispatchReply:
        """Check plugins, materialize the inputs sent along, then
        dispatch the segment

        Inputs already present on the client when the segment was
        dispatched are materialized before dispatch, so dispatchers
        find the files they may read at dispatch time, as they would
        on the client.

        Raises:
            MethodError: IncompatiblePlugin, if the segment would
                involve plugins incompatible with the client, or
                generic dispatchers differing from the client's.
        """
        from ..project.project import PlanRealization

        descriptor = self.descriptor
        self.passes_check()
        plan = descriptor.plan(get_plugin_registry().get_all_backends(), self.gbs_config, self.table)
        output_group = plan.output_group
        context = SegmentBuildContext(
            self.event_send, self.table,
            project=descriptor.project_model(self.table),
            gbs_config=self.gbs_config,
            semaphore=self.semaphore,
            base_output_path=self.table.path_of(descriptor.base_output),
            shared_cache_root=self.table.path_of(descriptor.shared_cache),
            parent_reporter=plan,
        )
        context.plan = plan
        context.set_output_group_context(
            topcell=output_group.topcell,
            topcell_library=descriptor.output_group.topcell_library,
            output_group=output_group,
        )
        generic = PlanRealization.generic_dispatchers_of(context, output_group)
        self.generic_check(generic)

        if descriptor.manifest is not None:
            await asyncio.to_thread(self.materialize, descriptor.manifest)
        inputs, goals = descriptor.pending_populate(context, self.table)

        for pm in plan.passes:
            for dispatcher in pm.pass_obj.dispatchers(context):
                context.register_dispatcher(dispatcher)
        PlanRealization.generic_dispatchers_register(context, generic)

        await context.run_dispatcher_iteration(max_iterations=PlanRealization.DISPATCH_ITERATIONS)
        self.context = context

        unproduced = [g for g in goals if not any(isinstance(d, Task) for d in g.depends_on)]
        if unproduced:
            raise MethodError("Unproduced", "Segment produces no " + ", ".join(
                f"{g.file_type} at {self.table.locate(g.path)}" for g in unproduced))

        given = set(inputs) | set(goals)
        exported = [
            r for r in context.iter_pending()
            if r not in given
            and r.typology != ResourceTypology.DEFINITION
            and ({r.file_type, *r.file_type_aliases} & descriptor.exported_types)
            and any(isinstance(d, Task) for d in r.depends_on)
        ]
        goal_descriptors = [ResourceDescriptor.from_resource(g, self.table) for g in goals]
        exported_descriptors = [ResourceDescriptor.from_resource(r, self.table) for r in exported]
        self.outputs = goal_descriptors + exported_descriptors
        return SegmentDispatchReply(
            id=self.id,
            goals=goal_descriptors,
            exported=exported_descriptors,
            pending_inputs=[
                index for index, r in enumerate(inputs)
                if context.get_pending(r.path) is r
            ],
        )

    def materialize(self, manifest: ContentManifest) -> None:
        """Bring the materialized inputs to the content of a manifest

        Files materialized before and listed with other content, or no
        longer listed, are removed; files listed identically are kept.
        Directories are never removed.
        """
        new = manifest.entries
        for location, entry in self.materialized.entries.items():
            if entry.kind == ManifestEntry.FILE and new.get(location) != entry:
                self.table.path_of(location).unlink(missing_ok=True)
        ContentManifest([
            e for e in new.values()
            if e.kind == ManifestEntry.DIRECTORY or self.materialized.entries.get(e.location) != e
        ]).materialize(self.table, self.blob_store, link=False)
        self.materialized = manifest

    async def execute(self, call: Call, manifest: ContentManifest) -> ContentManifest:
        """Materialize the inputs, build, and store the outputs

        Tool messages and step progress are sent as events of call.

        Returns:
            Content of the outputs, every blob of it in the blob store

        Raises:
            MethodError: BuildFailed, with the failure summary as data
                {report, headline}, when the build fails.
        """
        if self.context is None:
            raise MethodError("ProtocolError", f"Segment {self.id} is not dispatched")
        if self.executed:
            raise MethodError("ProtocolError", f"Segment {self.id} was already executed")
        self.executed = True

        try:
            await asyncio.to_thread(self.materialize, manifest)
            self.__call = call
            self.__events = asyncio.Queue()
            forwarder = asyncio.create_task(self.__events_forward())
            try:
                async with self.context.build():
                    await asyncio.gather(*self.context.running)
            except BaseException:
                forwarder.cancel()
                raise
            finally:
                self.__events.put_nowait(None)
                await asyncio.gather(forwarder, return_exceptions=True)
                self.__events = None
                self.__call = None

            if self.context.build_failed:
                headline = self.context.failure_headline or "Build failed"
                raise MethodError("BuildFailed", headline, {
                    "headline": headline,
                    "report": self.report_body(self.context.failure_report),
                })
            return await asyncio.to_thread(self.outputs_store)
        finally:
            if not self.keep:
                await asyncio.to_thread(shutil.rmtree, self.directory, True)

    @staticmethod
    def report_body(report: list[str]) -> list[str]:
        """Failure summary without its heading, which the client has its own of"""
        lines = list(report)
        while lines and lines[0] in ("", "Build Failed!"):
            lines.pop(0)
        return lines

    def outputs_store(self) -> ContentManifest:
        builder = ContentManifest.Builder(self.table)
        for descriptor in self.outputs:
            builder.tree_add(descriptor.location, descriptor.directory)
        manifest = ContentManifest(builder.entries.values())
        for entry in manifest.files:
            if self.blob_store.has(entry.sha256):
                continue
            digest = self.blob_store.file_add(self.table.path_of(entry.location))
            if digest != entry.sha256:
                raise WireError(f"{entry.location} changed while its outputs were stored")
        return manifest
