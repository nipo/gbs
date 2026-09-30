"""Serializable plan segment descriptors

A segment is a subset of the passes of a plan, run by another gbs
instance. Its descriptor carries what that instance needs to rebuild
an equivalent plan for these passes alone: the passes, the output group
settings the dispatchers read, the resources to start from and those to
produce, and the root table every location in it refers to.
"""

from __future__ import annotations
import copy
from pathlib import Path
from typing import Any, Iterable, Optional

from ..build.task import Resource
from ..planner.passes import PassMetadata
from ..project.model import OutputFile, OutputGroup, ProjectModel
from .manifest import ContentManifest
from .resource import ResourceDescriptor
from .roots import RootedPath, RootTable
from .wire import WireError, WireFormat, WireObject

__all__ = ["PassDescriptor", "OutputGroupDescriptor", "SegmentDescriptor"]


class PassDescriptor:
    """Identity of a planned pass

    A pass is recreated by asking its backend again, with the same
    configuration and the same requested output types, and picking the
    returned pass of the same name. Pass constructors only derive their
    state from the backend configuration, the project configuration and
    the gbs configuration, so the recreated pass is equivalent — except
    for what the gbs configuration contributes, which is the recreating
    host's own (its tools). The pass class is recorded to detect a
    backend that answers differently on the other side.

    Attributes:
        backend: Backend name
        name: Pass name
        pass_class: "module:qualname" of the pass class
        config: Backend configuration the pass was contributed with,
            target and tool overrides included
        requested_types: Output types the backend was asked for
    """

    def __init__(self, backend: str, name: str, pass_class: str,
                 config: dict[str, Any], requested_types: Iterable[str]):
        self.backend = backend
        self.name = name
        self.pass_class = pass_class
        self.config = config
        self.requested_types = frozenset(requested_types)

    @staticmethod
    def class_name(obj: Any) -> str:
        cls = type(obj)
        return f"{cls.__module__}:{cls.__qualname__}"

    @classmethod
    def from_metadata(cls, metadata: PassMetadata) -> PassDescriptor:
        """Describe a planned pass

        A pass planned on a remote host is described as that host
        contributed it.
        """
        from .planning import RemotePass
        if isinstance(metadata.pass_obj, RemotePass):
            return PassDescriptor.from_json(metadata.pass_obj.descriptor.to_json())
        return cls(
            backend=metadata.backend_name,
            name=metadata.name,
            pass_class=metadata.pass_class,
            config=WireFormat.json_check(
                metadata.config, f"configuration of pass {metadata.name}"),
            requested_types=metadata.requested_types,
        )

    def instantiate(self, backends: dict[str, Any],
                    project_config: dict[str, Any],
                    gbs_config: Any) -> PassMetadata:
        """Recreate the pass from its backend

        Args:
            backends: Available backends by name
            project_config: Project configuration passes are given
            gbs_config: GBS configuration of this host

        Raises:
            WireError: If the backend is unknown or does not contribute
                exactly one pass of that name and class.
        """
        backend = backends.get(self.backend)
        if backend is None:
            raise WireError(f"Pass {self.name}: backend {self.backend!r} is not available")
        config = copy.deepcopy(self.config)
        contributed = backend.contribute_passes(
            config, set(self.requested_types), project_config, gbs_config)
        matches = [p for p in contributed if p.name == self.name]
        if len(matches) != 1:
            raise WireError(
                f"Backend {self.backend} contributed {len(matches)} pass(es) "
                f"named {self.name!r}, expected one"
            )
        pass_obj, = matches
        if self.class_name(pass_obj) != self.pass_class:
            raise WireError(
                f"Pass {self.name} is {self.class_name(pass_obj)} here, "
                f"{self.pass_class} on the other side"
            )
        return PassMetadata(
            pass_obj=pass_obj,
            config=config,
            backend_name=self.backend,
            requested_types=set(self.requested_types),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "name": self.name,
            "class": self.pass_class,
            "config": self.config,
            "requested_types": sorted(self.requested_types),
        }

    @classmethod
    def from_json(cls, data: Any) -> PassDescriptor:
        reader = WireObject(data, "pass")
        descriptor = cls(
            backend=reader.field("backend", str),
            name=reader.field("name", str),
            pass_class=reader.field("class", str),
            config=reader.field("config", dict),
            requested_types=reader.string_list("requested_types"),
        )
        reader.finish()
        return descriptor


class OutputGroupDescriptor:
    """Output group settings a segment's dispatchers read

    Output file paths are root-relative locations.
    """

    def __init__(self, name: str, topcell: str, topcell_library: str,
                 target: dict[str, Any], backend_config: dict[str, Any],
                 exclude_dispatchers: list[str],
                 outputs: list[tuple[str, RootedPath]]):
        self.name = name
        self.topcell = topcell
        self.topcell_library = topcell_library
        self.target = target
        self.backend_config = backend_config
        self.exclude_dispatchers = exclude_dispatchers
        self.outputs = outputs

    @classmethod
    def from_output_group(cls, output_group: OutputGroup, topcell_library: str,
                          table: RootTable) -> OutputGroupDescriptor:
        what = f"output group {output_group.name}"
        return cls(
            name=output_group.name,
            topcell=output_group.topcell,
            topcell_library=topcell_library,
            target=WireFormat.json_check(output_group.target, f"{what} target"),
            backend_config=WireFormat.json_check(
                output_group.backend_config, f"{what} backend configuration"),
            exclude_dispatchers=list(output_group.exclude_dispatchers),
            outputs=[
                (output.type, table.locate(Path(output.path).resolve()))
                for output in output_group.outputs
            ],
        )

    def output_group(self, table: RootTable, filter_vars: dict[str, Any]) -> OutputGroup:
        """OutputGroup with outputs placed by table"""
        return OutputGroup(
            name=self.name,
            topcell=self.topcell,
            filter_vars=copy.deepcopy(filter_vars),
            backend_config=copy.deepcopy(self.backend_config),
            outputs=[OutputFile(type=t, path=table.path_of(loc)) for t, loc in self.outputs],
            target=copy.deepcopy(self.target),
            exclude_dispatchers=list(self.exclude_dispatchers),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "topcell": self.topcell,
            "topcell_library": self.topcell_library,
            "target": self.target,
            "backend_config": self.backend_config,
            "exclude_dispatchers": list(self.exclude_dispatchers),
            "outputs": [{"type": t, "location": loc.to_json()} for t, loc in self.outputs],
        }

    @classmethod
    def from_json(cls, data: Any) -> OutputGroupDescriptor:
        reader = WireObject(data, "output group")
        name = reader.field("name", str)
        topcell = reader.field("topcell", str)
        topcell_library = reader.field("topcell_library", str)
        target = reader.field("target", dict)
        backend_config = reader.field("backend_config", dict)
        exclude_dispatchers = reader.string_list("exclude_dispatchers")
        outputs = []
        for item in reader.field("outputs", list):
            output = WireObject(item, "output")
            file_type = output.field("type", str)
            location = RootedPath.from_json(output.field("location", dict))
            output.finish()
            outputs.append((file_type, location))
        reader.finish()
        return cls(name, topcell, topcell_library, target, backend_config,
                   exclude_dispatchers, outputs)


class SegmentDescriptor:
    """Everything needed to rebuild a plan for a subset of passes

    Attributes:
        project_name: Name of the project
        project_config: Raw project configuration passes are given
        output_group: Output group settings
        filter_vars: Filter variables of the plan
        passes: Passes of the segment, in plan order
        roots: Root table all locations refer to
        base_output: Base output directory of the build
        shared_cache: Shared cache root of the build
        inputs: Resources the segment starts from, in pending queue
            order
        dependencies: For each input, indices of the inputs it depends
            on (the partition dependencies of sources)
        goals: Resources the segment must produce
        exported_types: File types of the resources the segment
            produces that passes of other segments consume
        generic_plugins: Plugins whose generic dispatchers the client
            registers for the output group; the segment must register
            those of the same plugins
        manifest: Content of the inputs, None until it is computed
            (inputs produced by earlier passes may not exist when the
            segment is described)
    """

    def __init__(self, project_name: str, project_config: dict[str, Any],
                 output_group: OutputGroupDescriptor,
                 filter_vars: dict[str, Any],
                 passes: list[PassDescriptor],
                 roots: RootTable,
                 base_output: RootedPath,
                 shared_cache: RootedPath,
                 inputs: list[ResourceDescriptor],
                 dependencies: list[list[int]],
                 goals: list[ResourceDescriptor],
                 exported_types: Iterable[str],
                 generic_plugins: Iterable[str],
                 manifest: Optional[ContentManifest] = None):
        if len(dependencies) != len(inputs):
            raise WireError(
                f"segment: {len(dependencies)} dependency lists for {len(inputs)} inputs")
        for index, deps in enumerate(dependencies):
            for dep in deps:
                if not isinstance(dep, int) or isinstance(dep, bool) \
                        or not 0 <= dep < len(inputs) or dep == index:
                    raise WireError(f"segment: input {index} depends on invalid input {dep!r}")
        self.project_name = project_name
        self.project_config = project_config
        self.output_group = output_group
        self.filter_vars = filter_vars
        self.passes = passes
        self.roots = roots
        self.base_output = base_output
        self.shared_cache = shared_cache
        self.inputs = inputs
        self.dependencies = [sorted(set(d)) for d in dependencies]
        self.goals = goals
        self.exported_types = frozenset(exported_types)
        self.generic_plugins = frozenset(generic_plugins)
        self.manifest = manifest

    @classmethod
    def from_realization(cls, realization: Any, passes: Iterable[PassMetadata],
                         inputs: Iterable[Resource], goals: Iterable[Resource],
                         roots: RootTable,
                         manifest: Optional[ContentManifest] = None,
                         exported_types: Iterable[str] = (),
                         generic_plugins: Iterable[str] = ()) -> SegmentDescriptor:
        """Describe some passes of a PlanRealization

        Args:
            realization: PlanRealization the passes belong to
            passes: Passes of the segment, from realization.plan.passes
            inputs: Resources handed to the segment, pending in the
                realization's build context
            goals: Resources the segment must produce
            roots: Local root table of the realization
            manifest: Content of the inputs, if already computed
            exported_types: Types of the produced resources other
                segments consume
            generic_plugins: Plugins whose generic dispatchers the
                realization registers
        """
        plan = realization.plan
        ctx = realization.build_ctx
        passes = list(passes)
        inputs = list(inputs)
        for pm in passes:
            if not any(pm is planned for planned in plan.passes):
                raise WireError(f"Pass {pm.name} is not part of the plan")
        index_of = {resource.path: index for index, resource in enumerate(inputs)}
        dependencies: list[list[int]] = [[] for _ in inputs]
        for index, resource in enumerate(inputs):
            for dependent in ctx.get_pending_dependents(resource.path):
                other = index_of.get(dependent.path)
                if other is not None and other != index:
                    dependencies[other].append(index)
        return cls(
            project_name=realization.project.model.name,
            project_config=WireFormat.json_check(
                realization.project.model.raw_config, "project configuration"),
            output_group=OutputGroupDescriptor.from_output_group(
                plan.output_group, realization.build_ctx.get_topcell_library(), roots),
            filter_vars=WireFormat.json_check(plan.filter_vars, "filter variables"),
            passes=[PassDescriptor.from_metadata(pm) for pm in passes],
            roots=roots,
            base_output=roots.locate(ctx.base_output_path.resolve()),
            shared_cache=roots.locate(ctx.shared_cache_root.resolve()),
            inputs=[ResourceDescriptor.from_resource(r, roots) for r in inputs],
            dependencies=dependencies,
            goals=[ResourceDescriptor.from_resource(r, roots) for r in goals],
            exported_types=exported_types,
            generic_plugins=generic_plugins,
            manifest=manifest,
        )

    def passes_instantiate(self, backends: Iterable[Any], gbs_config: Any) -> list[PassMetadata]:
        """Recreate the segment's passes from the backends of this host"""
        by_name: dict[str, Any] = {}
        for backend in backends:
            if backend.name in by_name:
                raise WireError(f"Backend {backend.name!r} is registered twice")
            by_name[backend.name] = backend
        return [
            p.instantiate(by_name, copy.deepcopy(self.project_config), gbs_config)
            for p in self.passes
        ]

    def project_model(self, table: RootTable) -> ProjectModel:
        """Project model holding the segment's output group alone

        Args:
            table: Root table placed on this host
        """
        return ProjectModel(
            name=self.project_name,
            root_partition_templates={},
            output_groups=[self.output_group.output_group(table, self.filter_vars)],
            raw_config=copy.deepcopy(self.project_config),
        )

    def plan(self, backends: Iterable[Any], gbs_config: Any, table: RootTable,
             parent_reporter: Any = None) -> Any:
        """BuildPlan of the segment's passes, without repositories

        Args:
            backends: Backends of this host
            gbs_config: GBS configuration of this host
            table: Root table placed on this host
            parent_reporter: Optional parent UIReporter
        """
        from ..planner.planner import BuildPlan
        passes = self.passes_instantiate(backends, gbs_config)
        types_with_library: set[str] = set()
        for pm in passes:
            types_with_library |= set(pm.types_with_library)
        return BuildPlan(
            output_group=self.output_group.output_group(table, self.filter_vars),
            passes=passes,
            filter_vars=copy.deepcopy(self.filter_vars),
            repositories=[],
            types_with_library=types_with_library,
            parent_reporter=parent_reporter,
        )

    def resources_get(self, context: Any, table: RootTable) -> tuple[list[Resource], list[Resource]]:
        """Inputs and goals of the segment as resources of a build context

        Args:
            context: BuildContext to register the resources in
            table: Root table placed on this host
        """
        return (
            [d.resource_get(context, table) for d in self.inputs],
            [d.resource_get(context, table) for d in self.goals],
        )

    def pending_populate(self, context: Any, table: RootTable) -> tuple[list[Resource], list[Resource]]:
        """Queue the inputs, with their dependencies, and the goals

        Args:
            context: BuildContext whose pending queue to fill
            table: Root table placed on this host

        Returns:
            Inputs and goals, as resources of the context
        """
        inputs, goals = self.resources_get(context, table)
        for resource, deps in zip(inputs, self.dependencies):
            context.add_pending(resource, source_dependencies={inputs[i] for i in deps} or None)
        for goal in goals:
            context.add_pending(goal)
        return inputs, goals

    def to_json(self) -> dict[str, Any]:
        return {
            "version": WireFormat.VERSION,
            "project_name": self.project_name,
            "project_config": self.project_config,
            "output_group": self.output_group.to_json(),
            "filter_vars": self.filter_vars,
            "passes": [p.to_json() for p in self.passes],
            "roots": self.roots.to_json(),
            "base_output": self.base_output.to_json(),
            "shared_cache": self.shared_cache.to_json(),
            "inputs": [d.to_json() for d in self.inputs],
            "dependencies": [list(d) for d in self.dependencies],
            "goals": [d.to_json() for d in self.goals],
            "exported_types": sorted(self.exported_types),
            "generic_plugins": sorted(self.generic_plugins),
            "manifest": None if self.manifest is None else self.manifest.to_json(),
        }

    @classmethod
    def from_json(cls, data: Any) -> SegmentDescriptor:
        reader = WireObject(data, "segment")
        WireFormat.version_check(reader)
        project_name = reader.field("project_name", str)
        project_config = reader.field("project_config", dict)
        output_group = OutputGroupDescriptor.from_json(reader.field("output_group", dict))
        filter_vars = reader.field("filter_vars", dict)
        passes = [PassDescriptor.from_json(p) for p in reader.field("passes", list)]
        roots = RootTable.from_json(reader.field("roots", list))
        base_output = RootedPath.from_json(reader.field("base_output", dict))
        shared_cache = RootedPath.from_json(reader.field("shared_cache", dict))
        inputs = [ResourceDescriptor.from_json(d) for d in reader.field("inputs", list)]
        dependencies = reader.field("dependencies", list)
        for deps in dependencies:
            if not isinstance(deps, list):
                raise WireError("segment: dependencies must be lists of input indices")
        goals = [ResourceDescriptor.from_json(d) for d in reader.field("goals", list)]
        exported_types = reader.string_list("exported_types")
        generic_plugins = reader.string_list("generic_plugins")
        manifest_data = reader.field("manifest", dict, type(None))
        reader.finish()

        segment = cls(
            project_name=project_name,
            project_config=project_config,
            output_group=output_group,
            filter_vars=filter_vars,
            passes=passes,
            roots=roots,
            base_output=base_output,
            shared_cache=shared_cache,
            inputs=inputs,
            dependencies=dependencies,
            goals=goals,
            exported_types=exported_types,
            generic_plugins=generic_plugins,
            manifest=None if manifest_data is None else ContentManifest.from_json(manifest_data),
        )
        segment.locations_check()
        return segment

    def locations_check(self) -> None:
        """Refuse any location referring to a root the table lacks"""
        locations: list[RootedPath] = [loc for _, loc in self.output_group.outputs]
        locations += [self.base_output, self.shared_cache]
        for descriptor in self.inputs + self.goals:
            locations.extend(loc for loc, _ in descriptor.trees())
        if self.manifest is not None:
            locations.extend(self.manifest.entries)
        for location in locations:
            if location.root not in self.roots.roots:
                raise WireError(f"segment: {location} refers to an unknown root")
