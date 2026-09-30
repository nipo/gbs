"""Running plan segments on a remote host

Passes planned on a remote host are grouped into segments. Each
segment is dispatched by one RemoteSegmentDispatcher, which hands the
segment's inputs to the remote gbs instance, lets it dispatch the
passes with its own backends, and stands for everything the segment
produces with one RemoteSegmentTask. Up-to-date checks stay local: a
segment whose outputs are newer than its inputs is not run, and the
remote is not asked to.
"""

from __future__ import annotations
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

from ..base.dispatcher import BaseDispatcher
from ..build.task import BuildError, ConfigurationError, Resource, ResourceTypology, Task
from ..build.type_aliases import sibling_aliases
from ..planner.passes import PassMetadata
from ..plugins import get_plugin_registry
from ..ui.messages import MessageSeverity, ToolMessage
from .channel import ChannelError
from .manifest import BlobStore, ContentManifest, ManifestEntry
from .peer import Event, RemoteError
from .planning import RemotePass
from .resource import ResourceDescriptor
from .roots import RootedPath, RootTable
from .segment import SegmentDescriptor
from .segment_run import SegmentDispatchReply
from .transfer import BlobTransfer
from .wire import WireError, WireObject

__all__ = [
    "RemoteSegmentFailure", "PlanSegment", "PlanSegments", "OutputInstaller",
    "RemoteSegmentDispatcher", "RemoteSegmentTask",
]


class RemoteSegmentFailure(BuildError):
    """The build of a segment failed on its remote host

    Carries the remote failure summary as report and headline.
    """
    pass


class PlanSegment:
    """Passes of a plan that run together on one remote host

    Attributes:
        index: Position among the segments of the plan
        host: Name of the host the passes run on
        passes: Passes of the segment, in plan order
        upstream: Segments producing, directly or through other
            passes, something this one consumes
        dispatched: Whether the segment was dispatched
    """

    def __init__(self, index: int, host: str, passes: list[PassMetadata]):
        self.index = index
        self.host = host
        self.passes = passes
        self.upstream: list[PlanSegment] = []
        self.dispatched = False

    @property
    def input_types(self) -> set[str]:
        return set().union(*(pm.input_types for pm in self.passes))

    @property
    def output_types(self) -> set[str]:
        return set().union(*(pm.output_types for pm in self.passes))

    def __repr__(self) -> str:
        return f"PlanSegment({self.index} on {self.host}: {', '.join(pm.name for pm in self.passes)})"


class PlanSegments:
    """The remote segments of a plan

    Passes are linked by data flow: a pass feeds another when one of
    its output types is an input type of the other. A segment holds
    passes of one host, and no path through passes of other hosts may
    leave a segment and come back to it: the segment runs as a whole,
    so it could not wait for what it feeds.

    Each pass gets a level: the most times a data flow path reaching
    it leaves a remote host for another pass. Remote passes of a host
    at the same level form a segment. A path leaving a segment raises
    the level of every pass after it, so it never comes back to that
    segment.

    Attributes:
        plan: The plan
        segments: Remote segments, in plan order of their first pass
    """

    def __init__(self, plan: Any):
        self.plan = plan
        self.segments: list[PlanSegment] = []
        self.__segment_of: dict[int, PlanSegment] = {}
        passes = list(plan.passes)
        if any(self.host_of(pm) is not None for pm in passes):
            self.__split(passes)

    @staticmethod
    def host_of(pm: PassMetadata) -> Optional[str]:
        """Host a pass runs on, None for the local host"""
        return pm.pass_obj.host if isinstance(pm.pass_obj, RemotePass) else None

    @staticmethod
    def types_expand(types: Iterable[str]) -> set[str]:
        """Types with their terminal-type aliases"""
        result = set()
        for t in types:
            result.add(t)
            result |= sibling_aliases(t)
        return result

    def segment_of(self, pm: PassMetadata) -> Optional[PlanSegment]:
        """Segment a pass of the plan belongs to, None for a local pass"""
        return self.__segment_of.get(id(pm))

    @classmethod
    def order(cls, passes: list[PassMetadata]) -> tuple[list[int], dict[int, set[int]]]:
        """Data flow order of passes

        Returns:
            Pass indices, each after the passes feeding it, and the
            indices of the passes feeding each pass

        Raises:
            ConfigurationError: If data flow between passes loops.
        """
        outputs = [cls.types_expand(pm.output_types) for pm in passes]
        inputs = [cls.types_expand(pm.input_types) for pm in passes]
        feeders = {
            i: {j for j in range(len(passes)) if j != i and outputs[j] & inputs[i]}
            for i in range(len(passes))
        }
        order: list[int] = []
        remaining = dict(feeders)
        while remaining:
            ready = sorted(i for i, f in remaining.items() if not (f & remaining.keys()))
            if not ready:
                raise ConfigurationError(
                    "Cannot split the plan between hosts: data flow loops between passes "
                    + ", ".join(passes[i].name for i in sorted(remaining))
                )
            for i in ready:
                order.append(i)
                del remaining[i]
        return order, feeders

    def __split(self, passes: list[PassMetadata]) -> None:
        order, feeders = self.order(passes)
        hosts = [self.host_of(pm) for pm in passes]
        levels: dict[int, int] = {}
        ancestors: dict[int, set[int]] = {}
        for i in order:
            level = 0
            ancestry: set[int] = set()
            for j in feeders[i]:
                leaves = hosts[j] is not None and hosts[j] != hosts[i]
                level = max(level, levels[j] + (1 if leaves else 0))
                ancestry |= {j} | ancestors[j]
            levels[i] = level
            ancestors[i] = ancestry

        by_key: dict[tuple[str, int], PlanSegment] = {}
        for i, pm in enumerate(passes):
            if hosts[i] is None:
                continue
            key = (hosts[i], levels[i])
            segment = by_key.get(key)
            if segment is None:
                segment = PlanSegment(len(self.segments), hosts[i], [])
                by_key[key] = segment
                self.segments.append(segment)
            segment.passes.append(pm)
            self.__segment_of[id(pm)] = segment

        index_of = {id(pm): i for i, pm in enumerate(passes)}
        for segment in self.segments:
            upstream: set[int] = set()
            for pm in segment.passes:
                for j in ancestors[index_of[id(pm)]]:
                    other = self.__segment_of.get(id(passes[j]))
                    if other is not None and other is not segment:
                        upstream.add(other.index)
            segment.upstream = [self.segments[i] for i in sorted(upstream)]


class OutputInstaller:
    """Writes outputs received from a remote host to their local paths

    Each output replaces what is at its path at once: a file is
    written beside its destination and renamed over it, a directory
    is built beside its destination and swapped in.
    """

    def __init__(self, table: RootTable, manifest: ContentManifest, store: BlobStore):
        self.table = table
        self.manifest = manifest
        self.store = store

    def entries_of(self, outputs: list[ResourceDescriptor]) -> dict[int, list[tuple[Path, ManifestEntry]]]:
        """Manifest entries of each output, as (local path, entry)

        Raises:
            WireError: If an entry lies in no output, or an output has
                no entry of its kind.
        """
        destinations = [self.table.path_of(d.location) for d in outputs]
        result: dict[int, list[tuple[Path, ManifestEntry]]] = {i: [] for i in range(len(outputs))}
        for entry in self.manifest.entries.values():
            path = self.table.path_of(entry.location)
            owners = [
                i for i, (d, dest) in enumerate(zip(outputs, destinations))
                if path == dest or (d.directory and path.is_relative_to(dest))
            ]
            if not owners:
                raise WireError(f"Remote output {entry.location} is not part of any expected output")
            for i in owners:
                result[i].append((path, entry))
        for i, (d, dest) in enumerate(zip(outputs, destinations)):
            kind = ManifestEntry.DIRECTORY if d.directory else ManifestEntry.FILE
            if not any(p == dest and e.kind == kind for p, e in result[i]):
                raise WireError(f"Remote outputs lack {kind} {d.location}")
        return result

    def install(self, outputs: list[ResourceDescriptor]) -> None:
        for i, entries in self.entries_of(outputs).items():
            dest = self.table.path_of(outputs[i].location)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if outputs[i].directory:
                self.directory_install(dest, entries)
            else:
                (_, entry), = entries
                self.file_install(dest, entry)

    def file_write(self, dest: Path, entry: ManifestEntry) -> None:
        shutil.copyfile(self.store.path(entry.sha256), dest)
        dest.chmod(0o755 if entry.executable else 0o644)

    def file_install(self, dest: Path, entry: ManifestEntry) -> None:
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
        os.close(fd)
        tmp = Path(tmp)
        try:
            self.file_write(tmp, entry)
            os.replace(tmp, dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def directory_install(self, dest: Path, entries: list[tuple[Path, ManifestEntry]]) -> None:
        tmp = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f".{dest.name}."))
        try:
            ordered = sorted(entries, key=lambda pe: len(pe[0].parts))
            for path, entry in ordered:
                target = tmp / path.relative_to(dest)
                if entry.kind == ManifestEntry.DIRECTORY:
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    self.file_write(target, entry)
            tmp.chmod(0o755)
            if dest.is_dir() and not dest.is_symlink():
                old = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f".{dest.name}.old."))
                os.replace(dest, old / dest.name)
                os.replace(tmp, dest)
                shutil.rmtree(old)
            else:
                if dest.exists() or dest.is_symlink():
                    dest.unlink()
                os.replace(tmp, dest)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise


class RemoteSegmentDispatcher(BaseDispatcher):
    """Dispatches a plan segment on its remote host

    Waits for the pending queue to settle, so every input of the
    segment is there, and for the segments upstream to be dispatched.
    It then claims:

    - pending resources of the segment input types;
    - DEFINITION resources inside the root table, except the local
      GBS configuration files, which describe local tools, and
      generated ones (the configuration fingerprint), which stay local
      dependencies of the segment task;
    - output goals of the segment output types that no other pass
      produces.

    The plugins of the segment passes, and those whose generic
    dispatchers are registered locally, must be compatible with the
    host, which registers the generic dispatchers of the same plugins.

    The remote dispatches the segment and answers with what it
    produces: the goals, and resources of the types other passes
    consume. A RemoteSegmentTask then stands for the whole segment.
    """

    def __init__(self, context: Any, realization: Any, segment: PlanSegment):
        super().__init__(context, f"remote-segment-{segment.index}", tool_name="remote-segment")
        self.realization = realization
        self.segment = segment
        self.task: Optional[RemoteSegmentTask] = None
        self.known: set[Path] = set()

    @property
    def other_passes(self) -> list[PassMetadata]:
        return [pm for pm in self.realization.plan.passes
                if not any(pm is own for own in self.segment.passes)]

    def inputs_select(self) -> list[Resource]:
        types = PlanSegments.types_expand(self.segment.input_types)
        return [
            r for r in self.context.filter_pending(file_type=sorted(types))
            if r.typology not in (ResourceTypology.OUTPUT, ResourceTypology.DEFINITION)
        ]

    def definitions_select(self, table: RootTable) -> tuple[list[Resource], list[Resource]]:
        """DEFINITION resources to transfer, and those staying local"""
        gbs_config = self.context.gbs_config
        local_files = {Path(p).resolve() for p in gbs_config.loaded_files} if gbs_config else set()
        sent, local = [], []
        for r in self.context.filter_pending(typology=ResourceTypology.DEFINITION):
            if r.depends_on or r.path in local_files or table.root_of(r.path) is None:
                local.append(r)
            else:
                sent.append(r)
        return sent, local

    def goals_select(self) -> list[Resource]:
        from ..planner.planner import strip_type_suffixes
        produced = PlanSegments.types_expand(self.segment.output_types)
        elsewhere = PlanSegments.types_expand(
            set().union(*(pm.output_types for pm in self.other_passes)))
        goals = []
        for r in self.context.get_pending_unsatisfied_outputs():
            types = {t for t in (r.file_type, *r.file_type_aliases) if t is not None}
            types |= {strip_type_suffixes(t) for t in types}
            if types & produced and not types & elsewhere:
                goals.append(r)
        return goals

    def exported_types(self) -> set[str]:
        consumed = PlanSegments.types_expand(
            set().union(*(pm.input_types for pm in self.other_passes)))
        return PlanSegments.types_expand(self.segment.output_types) & consumed

    async def process(self) -> None:
        if not self.segment.dispatched:
            return
        late = [r for r in self.inputs_select() if r.path not in self.known]
        if late:
            raise ConfigurationError(
                f"Segment {self.segment.index} on {self.segment.host} is already dispatched "
                f"when inputs appear: " + ", ".join(str(r.path) for r in late)
            )

    async def process_settled(self) -> None:
        if self.segment.dispatched or not all(s.dispatched for s in self.segment.upstream):
            return
        self.segment.dispatched = True

        host = self.realization.project.remote_host(self.segment.host)
        what = f"{', '.join(pm.name for pm in self.segment.passes)} on {host.name}"
        generic = self.realization.generic_dispatchers
        try:
            await self.plugins_check(host, what, generic)
            table = RootTable.from_realization(self.realization)
            inputs = self.inputs_select()
            definitions, local = self.definitions_select(table)
            transferred = inputs + definitions
            goals = self.goals_select()
            descriptor = SegmentDescriptor.from_realization(
                self.realization, self.segment.passes, transferred, goals, table,
                exported_types=self.exported_types(), generic_plugins=generic)
            present = [d for d in descriptor.inputs if self.present(table, d)]
            descriptor.manifest = await asyncio.to_thread(ContentManifest.compute, table, present)
            await BlobTransfer(host.peer).upload(RemoteSegmentTask.files(table, descriptor.manifest))
            answer = await host.peer.request("segment.dispatch", descriptor.to_json())
            reply = SegmentDispatchReply.from_json(answer.result)
            if {d.location for d in reply.goals} != {d.location for d in descriptor.goals}:
                raise WireError(f"{host.name} answered with other goals")
        except (RemoteError, WireError) as e:
            message = e.message if isinstance(e, RemoteError) else str(e)
            raise ConfigurationError(f"Cannot dispatch {what}: {message}") from e
        except ChannelError as e:
            raise ConfigurationError(host.failure_describe(f"Cannot dispatch {what}: {e}")) from e

        if not reply.goals and not reply.exported:
            raise ConfigurationError(f"{what} produce nothing the build uses")

        outputs = reply.goals + reply.exported
        task = RemoteSegmentTask(self, host, reply.id, descriptor, outputs, table, what)
        pending = set(reply.pending_inputs)
        for index, resource in enumerate(transferred):
            task.add_input(resource, consume=index not in pending)
        for resource in local:
            task.add_input(resource, consume=False)
        for goal in goals:
            task.add_output(goal)
            goal.generated_by = self.name
        for d in reply.exported:
            task.add_output(d.resource_get(self.context, table))
        self.task = task
        self.known = {r.path for r in transferred} | {table.path_of(d.location) for d in outputs}

    async def plugins_check(self, host: Any, what: str, generic: dict[str, list[str]]) -> None:
        """Refuse a segment involving plugins incompatible with its host

        Args:
            host: RemoteHost the segment runs on
            what: The segment, for messages
            generic: Names of the generic dispatchers registered
                locally, by plugin

        Raises:
            ConfigurationError: Naming each incompatible plugin, why,
                and what to do about it.
        """
        registry = get_plugin_registry()
        remedies: dict[str, str] = {}
        for pm in self.segment.passes:
            plugin = registry.backend_plugin(pm.backend_name)
            if plugin is None:
                raise ConfigurationError(
                    f"Cannot dispatch {what}: backend {pm.backend_name} belongs to no plugin here")
            remedies[plugin] = f"install or update it on {host.name}"
        for plugin, names in generic.items():
            remedies.setdefault(plugin, (
                f"install or update it on {host.name}, or exclude its dispatchers "
                f"with exclude_dispatchers: [{', '.join(names)}]"))
        lines = []
        for plugin in sorted(remedies):
            problem = await host.plugin_problem(plugin)
            if problem:
                lines.append(f"  {problem[0]}: {remedies[plugin]}")
                lines.extend(f"    {line}" for line in problem[1:])
        if lines:
            raise ConfigurationError(f"Cannot dispatch {what}:\n" + "\n".join(lines))

    @staticmethod
    def present(table: RootTable, descriptor: ResourceDescriptor) -> bool:
        """Whether every tree of a resource exists on this host"""
        for location, is_directory in descriptor.trees():
            path = table.path_of(location)
            if not (path.is_dir() if is_directory else path.is_file()):
                return False
        return True


class RemoteSegmentTask(Task):
    """Runs a dispatched segment on its remote host

    Sends the inputs the remote lacks, has it build, relays its tool
    messages and progress, and writes the outputs it produced.
    """

    def __init__(self, dispatcher: RemoteSegmentDispatcher, host: Any, remote_id: int,
                 descriptor: SegmentDescriptor, outputs: list[ResourceDescriptor],
                 table: RootTable, description: str):
        super().__init__(dispatcher, f"remote-segment-{dispatcher.segment.index}",
                         description=description)
        self.host = host
        self.remote_id = remote_id
        self.descriptor = descriptor
        self.output_descriptors = outputs
        self.table = table

    @staticmethod
    def files(table: RootTable, manifest: ContentManifest) -> dict[str, Path]:
        """A local file for each blob of a manifest"""
        return {e.sha256: table.path_of(e.location) for e in manifest.files}

    async def work(self) -> None:
        peer = self.host.peer
        transfer = BlobTransfer(peer)
        manifest = await asyncio.to_thread(ContentManifest.compute, self.table, self.descriptor.inputs)
        sent = await transfer.upload(self.files(self.table, manifest))
        self.info(f"Sent {sent} blob(s) to {self.host.name}")

        events: asyncio.Queue = asyncio.Queue()
        relay = asyncio.create_task(self.events_relay(events))
        try:
            reply = await peer.request(
                "segment.execute", {"id": self.remote_id, "manifest": manifest.to_json()},
                on_event=events.put_nowait)
        except RemoteError as e:
            if e.type == "BuildFailed":
                reader = WireObject(e.data, "BuildFailed data")
                headline = reader.field("headline", str)
                report = reader.string_list("report")
                reader.finish()
                raise RemoteSegmentFailure(
                    f"{self.host.name}: {headline}", report=report,
                    headline=f"{self.host.name}: {headline}") from e
            raise BuildError(f"{self.host.name}: {e.message}") from e
        except ChannelError as e:
            raise BuildError(self.host.failure_describe(f"{self.host.name}: {e}")) from e
        finally:
            events.put_nowait(None)
            await relay

        reader = WireObject(reply.result, "segment.execute result")
        outputs = ContentManifest.from_json(reader.field("manifest", dict))
        reader.finish()

        base = self.context.base_output_path
        base.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=base, prefix=".remote-blobs-"))
        try:
            store = BlobStore(staging)
            received = await transfer.download(outputs.digests(), store)
            self.info(f"Received {received} blob(s) from {self.host.name}")
            installer = OutputInstaller(self.table, outputs, store)
            await asyncio.to_thread(installer.install, self.output_descriptors)
        finally:
            await asyncio.to_thread(shutil.rmtree, staging, True)

    async def events_relay(self, events: asyncio.Queue) -> None:
        """Relay remote events until the None that ends them"""
        while (event := await events.get()) is not None:
            try:
                await self.event_relay(event)
            except (WireError, ValueError) as e:
                self.warning(f"Malformed event {event.name!r} from {self.host.name}: {e}")

    async def event_relay(self, event: Event) -> None:
        if event.name == "message":
            await self.add_message_obj(self.message_decode(event.data))
        elif event.name == "progress":
            reader = WireObject(event.data, "progress event")
            completed = reader.field("completed", int)
            total = reader.field("total", int)
            step = reader.field("step", str)
            reader.finish()
            fraction = completed / total if total else 0.0
            await self.update_progress(min(fraction, 0.99), f"{self.host.name}: {step}")
        else:
            self.debug(f"Ignoring event {event.name!r} from {self.host.name}")

    def message_decode(self, data: Any) -> ToolMessage:
        reader = WireObject(data, "message event")
        severity = MessageSeverity(reader.field("severity", str))
        text = reader.field("message", str)
        identifier = reader.field("identifier", str, type(None))
        extended = reader.field("extended_message", str, type(None))
        location = reader.field("file", dict, str, type(None))
        line = reader.field("line", int, type(None))
        column = reader.field("column", int, type(None))
        step = reader.field("step", str, type(None))
        reader.finish()
        self.debug(f"Message of {step} on {self.host.name}: {text}")
        if isinstance(location, dict):
            file_path: Optional[Path] = self.table.path_of(RootedPath.from_json(location))
        elif isinstance(location, str):
            file_path = Path(f"{self.host.name}:{location}")
        else:
            file_path = None
        return ToolMessage(
            severity=severity, message=text, identifier=identifier,
            extended_message=extended, file_path=file_path, line=line, column=column,
        )
