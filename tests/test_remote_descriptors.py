"""Tests for the host-independent descriptors of remote execution"""

import json
import os
import stat
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from gbs.build import BuildContext
from gbs.build.task import ResourceTypology
from gbs.planner.passes import PassMetadata
from gbs.planner.planner import BuildPlan, BuildPlanner
from gbs.plugins import get_plugin_registry
from gbs.project.model import OutputFile, OutputGroup, ProjectModel
from gbs.remote import (
    BlobStore, ContentManifest, ManifestEntry, PassDescriptor,
    ResourceDescriptor, ResourceMetadataCodec, RootedPath, RootTable,
    SegmentDescriptor, WireError, WireFormat,
)
from gbs.repository.model import Repository, SourceFile, SourceFileSet, Partition


def wire(data):
    """Send data through actual JSON text"""
    return json.loads(json.dumps(data))


class FakeRepository(Repository):
    def __init__(self, name, root, file_types=()):
        super().__init__(name, root)
        self.types = set(file_types)

    def file_types(self):
        return self.types

    def partition_lookup(self, partition_name, filter_vars):
        return None


@pytest.fixture
def layout(tmp_path):
    """Project with a nested repository, output tree and cache"""
    project = tmp_path / "proj"
    repo = project / "lib" / "repo"
    output = project / "gbs-build"
    cache = output / "cache"
    for d in (repo, output, cache):
        d.mkdir(parents=True)
    table = RootTable.for_build(
        project_dir=project,
        repositories=[FakeRepository("repo", repo)],
        base_output_path=output,
        shared_cache_root=cache,
    )
    return SimpleNamespace(project=project, repo=repo, output=output,
                           cache=cache, table=table, tmp=tmp_path)


class TestRootTable:
    def test_innermost_root_wins(self, layout):
        t = layout.table
        assert t.locate(layout.project / "top.vhd") == RootedPath("project", PurePosixPath("top.vhd"))
        assert t.locate(layout.repo / "a" / "b.vhd") == RootedPath("repo-0", PurePosixPath("a/b.vhd"))
        assert t.locate(layout.output / "g" / "x") == RootedPath("output", PurePosixPath("g/x"))
        assert t.locate(layout.cache / "ghdl" / "y") == RootedPath("cache", PurePosixPath("ghdl/y"))
        assert t.locate(layout.repo) == RootedPath("repo-0", PurePosixPath("."))

    def test_outside_every_root_is_an_error(self, layout):
        with pytest.raises(WireError, match="outside"):
            layout.table.locate(layout.tmp / "elsewhere" / "f.vhd")

    def test_relative_path_is_an_error(self, layout):
        with pytest.raises(WireError):
            layout.table.locate(Path("f.vhd"))

    def test_same_path_twice_is_one_root(self, tmp_path):
        table = RootTable.for_build(tmp_path, [FakeRepository("r", tmp_path)],
                                    tmp_path / "out", tmp_path / "out" / "cache")
        assert sorted(table.roots) == ["cache", "output", "project"]

    def test_round_trip_places_nested_roots_inside_their_parent(self, layout, tmp_path):
        remote = RootTable.from_json(wire(layout.table.to_json()))
        base = tmp_path / "remote"
        placed = remote.placed(base)

        location = layout.table.locate(layout.repo / "a" / "b.vhd")
        assert placed.path_of(RootedPath.from_json(wire(location.to_json()))) == \
            base.resolve() / "roots" / "project" / "lib" / "repo" / "a" / "b.vhd"
        assert placed.path_of(layout.table.locate(layout.cache / "c")) == \
            base.resolve() / "roots" / "project" / "gbs-build" / "cache" / "c"

        # Located back on the remote side, against the same root
        remote_path = placed.path_of(location)
        assert placed.locate(remote_path) == location
        # And back to the local host
        assert layout.table.path_of(location) == layout.repo / "a" / "b.vhd"

    def test_unplaced_table_cannot_resolve(self, layout):
        remote = RootTable.from_json(wire(layout.table.to_json()))
        with pytest.raises(WireError, match="not placed"):
            remote.path_of(RootedPath("project", PurePosixPath("x")))

    def test_uncovered_paths_get_extra_roots(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        support = tmp_path / "support" / "include"
        support.mkdir(parents=True)
        (support / "sub").mkdir()
        loose = tmp_path / "loose" / "x.vhd"
        loose.parent.mkdir()
        loose.write_text("")

        table = RootTable.for_build(
            project, [], project / "out", project / "out" / "cache",
            covered=[support / "sub", support, loose, project / "top.vhd"],
        )
        assert table.locate(support / "sub" / "h.h").root == table.locate(support).root
        assert table.locate(loose).path == PurePosixPath("x.vhd")
        assert {r for r in table.roots if r.startswith("extra-")} == {"extra-0", "extra-1"}

    def test_from_realization_covers_sources_outside_repositories(self, tmp_path):
        project = tmp_path / "proj"
        repo = tmp_path / "nsl" / "lib"
        support = tmp_path / "nsl" / "build" / "support"
        for d in (project, repo, support):
            d.mkdir(parents=True)
        (project / "p.gbs.yaml").write_text("")
        fileset = SourceFileSet()
        fileset.add_partition(Partition("lib.p", sources=[
            SourceFile(repo / "a.c", "c", include_dirs=[support]),
        ]))
        og = OutputGroup(name="g", topcell="top",
                         outputs=[OutputFile("bitstream", tmp_path / "deliver" / "top.bit")])
        plan = SimpleNamespace(repositories=[FakeRepository("lib", repo)], output_group=og,
                               output_path=lambda output: output.path)
        realization = SimpleNamespace(
            project=SimpleNamespace(path=project / "p.gbs.yaml"),
            plan=plan,
            source_fileset=fileset,
            build_ctx=BuildContext(base_output_path=project / "gbs-build"),
        )

        table = RootTable.from_realization(realization)

        assert table.locate(repo / "a.c").root == "repo-0"
        assert table.locate(support / "h.h").root.startswith("extra-")
        assert table.locate(tmp_path / "deliver" / "top.bit").root.startswith("extra-")

    def test_strict_reading(self):
        with pytest.raises(WireError, match="unknown"):
            RootTable.from_json([{"id": "a", "label": "", "parent": None, "path": "/x"}])
        with pytest.raises(WireError, match="missing"):
            RootTable.from_json([{"id": "a", "label": ""}])
        with pytest.raises(WireError, match="normalized"):
            RootedPath.from_json({"root": "a", "path": "x/../../y"})
        with pytest.raises(WireError, match="absolute"):
            RootedPath.from_json({"root": "a", "path": "/etc/passwd"})
        with pytest.raises(WireError, match="root id"):
            RootTable.from_json([{"id": "..", "label": "", "parent": None}])
        with pytest.raises(WireError, match="unknown root"):
            RootTable.from_json([{"id": "a", "label": "",
                                  "parent": {"root": "b", "path": "x"}}])


class TestResourceDescriptor:
    async def test_round_trip(self, layout, tmp_path):
        ctx = BuildContext(base_output_path=layout.output)
        include = layout.repo / "include"
        resource = ctx.get_resource(
            layout.repo / "ip",
            file_type="quartus-ip",
            library="lib",
            file_type_version="2008",
            typology=ResourceTypology.SOURCE,
            generated_by="gen",
            metadata={"include_dirs": [include], "language": "vhdl"},
            legacy_file_type="old-ip",
            directory=True,
        )

        descriptor = ResourceDescriptor.from_resource(resource, layout.table)
        assert descriptor.metadata["include_dirs"] == [RootedPath("repo-0", PurePosixPath("include"))]

        received = ResourceDescriptor.from_json(wire(descriptor.to_json()))
        assert received == descriptor
        assert received.metadata == descriptor.metadata

        base = tmp_path / "remote"
        placed = RootTable.from_json(wire(layout.table.to_json())).placed(base)
        remote_ctx = BuildContext(base_output_path=base / "out")
        remote = received.resource_get(remote_ctx, placed)

        remote_repo = base.resolve() / "roots" / "project" / "lib" / "repo"
        assert remote.path == remote_repo / "ip"
        assert remote.directory is True
        assert remote.file_type == "quartus-ip"
        assert remote.file_type_aliases == {"old-ip"}
        assert remote.library == "lib"
        assert remote.file_type_version == "2008"
        assert remote.typology == ResourceTypology.SOURCE
        assert remote.generated_by == "gen"
        assert remote.metadata == {"include_dirs": [remote_repo / "include"], "language": "vhdl"}

    async def test_file_resource_defaults(self, layout):
        ctx = BuildContext(base_output_path=layout.output)
        resource = ctx.get_resource(layout.output / "g" / "top.bit", file_type="bitstream")
        descriptor = ResourceDescriptor.from_resource(resource, layout.table)
        data = wire(descriptor.to_json())
        assert data["directory"] is False
        assert data["typology"] == "intermediate"
        assert data["location"] == {"root": "output", "path": "g/top.bit"}
        assert ResourceDescriptor.from_json(data) == descriptor

    async def test_undeclared_metadata_is_refused(self, layout):
        ctx = BuildContext(base_output_path=layout.output)
        resource = ctx.get_resource(layout.project / "a.vhd", metadata={"sneaky": "/some/path"})
        with pytest.raises(WireError, match="not declared"):
            ResourceDescriptor.from_resource(resource, layout.table)

    async def test_path_metadata_outside_roots_is_refused(self, layout):
        ctx = BuildContext(base_output_path=layout.output)
        resource = ctx.get_resource(layout.project / "a.c",
                                    metadata={"include_dirs": [layout.tmp / "nowhere"]})
        with pytest.raises(WireError, match="outside"):
            ResourceDescriptor.from_resource(resource, layout.table)

    async def test_register_refuses_a_conflicting_kind(self):
        with pytest.raises(ValueError):
            ResourceMetadataCodec.register("include_dirs", ResourceMetadataCodec.STRING)

    async def test_strict_reading(self, layout):
        ctx = BuildContext(base_output_path=layout.output)
        resource = ctx.get_resource(layout.project / "a.vhd")
        data = wire(ResourceDescriptor.from_resource(resource, layout.table).to_json())

        with pytest.raises(WireError, match="unknown field"):
            ResourceDescriptor.from_json(dict(data, extra=1))
        missing = dict(data)
        del missing["library"]
        with pytest.raises(WireError, match="missing"):
            ResourceDescriptor.from_json(missing)
        with pytest.raises(WireError, match="typology"):
            ResourceDescriptor.from_json(dict(data, typology="bogus"))
        with pytest.raises(WireError, match="directory"):
            ResourceDescriptor.from_json(dict(data, directory=1))
        with pytest.raises(WireError, match="not declared"):
            ResourceDescriptor.from_json(dict(data, metadata={"x": "y"}))


@pytest.fixture
async def content(layout):
    """A directory resource, a file inside it, an include directory"""
    d = layout.repo / "ip"
    (d / "sub").mkdir(parents=True)
    (d / "empty").mkdir()
    (d / "a.v").write_text("module a; endmodule\n")
    (d / "sub" / "b.v").write_text("module b; endmodule\n")
    (d / "run.sh").write_text("#!/bin/sh\n")
    (d / "run.sh").chmod(0o755)
    (d / "link.v").symlink_to(d / "a.v")
    include = layout.project / "include"
    include.mkdir()
    (include / "h.h").write_text("#define H\n")

    ctx = BuildContext(base_output_path=layout.output)
    resources = [
        ctx.get_resource(d, file_type="ip", directory=True),
        ctx.get_resource(d / "sub" / "b.v", file_type="verilog"),
        ctx.get_resource(layout.project / "top.c", file_type="c",
                         metadata={"include_dirs": [include]}),
    ]
    (layout.project / "top.c").write_text("int main;\n")
    descriptors = [ResourceDescriptor.from_resource(r, layout.table) for r in resources]
    return SimpleNamespace(dir=d, include=include, descriptors=descriptors)


class TestContentManifest:
    async def test_entries(self, layout, content):
        manifest = ContentManifest.compute(layout.table, content.descriptors)

        def rel(entry):
            return (entry.location.root, entry.location.path.as_posix())

        assert sorted(rel(e) for e in manifest.files) == [
            ("project", "include/h.h"),
            ("project", "top.c"),
            ("repo-0", "ip/a.v"),
            ("repo-0", "ip/link.v"),
            ("repo-0", "ip/run.sh"),
            ("repo-0", "ip/sub/b.v"),
        ]
        assert sorted(rel(e) for e in manifest.directories) == [
            ("project", "include"),
            ("repo-0", "ip"),
            ("repo-0", "ip/empty"),
            ("repo-0", "ip/sub"),
        ]
        by_path = {rel(e)[1]: e for e in manifest.files}
        assert by_path["ip/run.sh"].executable
        assert not by_path["ip/a.v"].executable
        assert by_path["ip/link.v"].sha256 == by_path["ip/a.v"].sha256
        assert by_path["ip/a.v"].size == len("module a; endmodule\n")
        assert len(manifest.digests()) == 5

    async def test_round_trip(self, layout, content):
        manifest = ContentManifest.compute(layout.table, content.descriptors)
        received = ContentManifest.from_json(wire(manifest.to_json()))
        assert received.entries == manifest.entries

    async def test_version_is_checked(self, layout, content):
        data = wire(ContentManifest.compute(layout.table, content.descriptors).to_json())
        data["version"] = WireFormat.VERSION + 1
        with pytest.raises(WireError, match="version"):
            ContentManifest.from_json(data)

    async def test_duplicate_entries_are_refused(self):
        entry = {"location": {"root": "a", "path": "x"}, "kind": "directory"}
        with pytest.raises(WireError, match="twice"):
            ContentManifest.from_json({"version": WireFormat.VERSION, "entries": [entry, entry]})
        with pytest.raises(WireError, match="unknown field"):
            ContentManifest.from_json({"version": WireFormat.VERSION,
                                       "entries": [dict(entry, sha256="0" * 64)]})

    async def test_dangling_link_is_an_error(self, layout, content):
        (content.dir / "dangling").symlink_to(content.dir / "missing")
        with pytest.raises(WireError, match="Dangling"):
            ContentManifest.compute(layout.table, content.descriptors)

    async def test_link_loop_is_an_error(self, layout, content):
        (content.dir / "sub" / "loop").symlink_to(content.dir)
        with pytest.raises(WireError, match="loop"):
            ContentManifest.compute(layout.table, content.descriptors)

    async def test_missing_file_is_an_error(self, layout, content):
        (layout.project / "top.c").unlink()
        with pytest.raises(WireError, match="missing"):
            ContentManifest.compute(layout.table, content.descriptors)

    async def test_materialize(self, layout, content, tmp_path):
        manifest = ContentManifest.compute(layout.table, content.descriptors)
        store = BlobStore(tmp_path / "blobs")
        for entry in manifest.files:
            store.file_add(layout.table.path_of(entry.location))
        assert all(store.has(d) for d in manifest.digests())

        received = ContentManifest.from_json(wire(manifest.to_json()))
        base = tmp_path / "remote"
        placed = RootTable.from_json(wire(layout.table.to_json())).placed(base)
        received.materialize(placed, store)

        remote_ip = base / "roots" / "project" / "lib" / "repo" / "ip"
        assert (remote_ip / "a.v").read_text() == "module a; endmodule\n"
        assert (remote_ip / "sub" / "b.v").read_text() == "module b; endmodule\n"
        assert (remote_ip / "empty").is_dir()
        assert not (remote_ip / "link.v").is_symlink()
        assert (remote_ip / "link.v").read_text() == "module a; endmodule\n"
        assert (base / "roots" / "project" / "include" / "h.h").is_file()
        assert (base / "roots" / "project" / "top.c").is_file()

        assert os.stat(remote_ip / "run.sh").st_mode & stat.S_IXUSR
        assert not os.stat(remote_ip / "a.v").st_mode & stat.S_IXUSR
        # Linked from the store, except the executable, which is copied
        assert (remote_ip / "a.v").samefile(store.path(received.entries[
            placed.locate(remote_ip / "a.v")].sha256))
        assert os.stat(remote_ip / "run.sh").st_nlink == 1

    async def test_materialize_by_copy(self, layout, content, tmp_path):
        manifest = ContentManifest.compute(layout.table, content.descriptors)
        store = BlobStore(tmp_path / "blobs")
        for entry in manifest.files:
            store.file_add(layout.table.path_of(entry.location))
        placed = layout.table.placed(tmp_path / "remote")
        manifest.materialize(placed, store, link=False)
        remote_a = placed.path_of(layout.table.locate(content.dir / "a.v"))
        assert os.stat(remote_a).st_nlink == 1
        assert os.stat(remote_a).st_mode & 0o200

    async def test_materialize_needs_every_blob(self, layout, content, tmp_path):
        manifest = ContentManifest.compute(layout.table, content.descriptors)
        placed = layout.table.placed(tmp_path / "remote")
        with pytest.raises(WireError, match="lacks"):
            manifest.materialize(placed, BlobStore(tmp_path / "blobs"))

    async def test_blob_store(self, tmp_path):
        store = BlobStore(tmp_path / "blobs")
        digest = store.bytes_add(b"hello")
        assert store.path(digest) == tmp_path / "blobs" / digest[:2] / digest
        assert store.path(digest).read_bytes() == b"hello"
        with pytest.raises(WireError, match="hashes"):
            store.bytes_add(b"hello", digest="0" * 64)
        with pytest.raises(WireError, match="Invalid"):
            store.path("../../etc/passwd")


class RequestTriggeredPass:
    """Contributed for a type it does not produce, like a constraint
    generator triggered by the flow it feeds"""
    name = "gen-constraints"
    input_types = {"netlist"}
    output_types = {"constraints"}
    types_with_library = set()

    def __init__(self, config):
        self.config = config

    def filter_vars(self):
        return {}

    def probe(self):
        return None


class RequestTriggeredBackend:
    name = "test.request-triggered"

    def contribute_passes(self, config, output_types, project_config=None, gbs_config=None):
        if "bitstream" in output_types:
            return [RequestTriggeredPass(config)]
        return []


class TestSegmentDescriptor:
    @staticmethod
    def pass_metadata(backend, config, requested):
        contributed = backend.contribute_passes(config, requested, {}, None)
        return [PassMetadata(p, config, backend.name, requested) for p in contributed]

    @pytest.fixture
    def realization(self, layout):
        registry = get_plugin_registry()
        backends = {b.name: b for b in registry.get_all_backends()}
        target = {"part": "LFE5U-25F-6BG256C"}
        diamond_config = {"target": target, "vhdl_standard": "2008"}
        ghdl_config = {"target": target, "vhdl_standard": "2008"}
        passes = (
            self.pass_metadata(backends["gbs.builtin.diamond"], diamond_config, {"bitstream"})
            + self.pass_metadata(backends["gbs.builtin.ghdl"], ghdl_config, {"ghdl-cf"})
            + self.pass_metadata(RequestTriggeredBackend(), {}, {"bitstream"})
        )
        og = OutputGroup(
            name="synth",
            topcell="top",
            filter_vars={"vendor": "lattice"},
            backend_config={"gbs.builtin.diamond": {"vhdl_standard": "2008"}},
            outputs=[OutputFile("bitstream", layout.project / "top.bit")],
            target=target,
            exclude_dispatchers=["validation-report"],
        )
        model = ProjectModel(name="proj", root_partition_templates={},
                             output_groups=[og], raw_config={"name": "proj"})
        plan = BuildPlan(og, passes, {"vendor": "lattice", "tool": "diamond"},
                         [FakeRepository("repo", layout.repo)], set())
        ctx = BuildContext(project=model, base_output_path=layout.output)
        ctx.set_output_group_context(topcell="top", output_group=og)
        return SimpleNamespace(project=SimpleNamespace(model=model), plan=plan, build_ctx=ctx,
                               backends=list(backends.values()) + [RequestTriggeredBackend()])

    async def test_round_trip(self, layout, realization, tmp_path):
        ctx = realization.build_ctx
        (layout.repo / "top.vhd").write_text("")
        inputs = [ctx.get_resource(layout.repo / "top.vhd", file_type="vhdl", library="work",
                                   typology=ResourceTypology.SOURCE)]
        goals = [ctx.get_resource(layout.project / "top.bit", file_type="bitstream",
                                  typology=ResourceTypology.OUTPUT)]
        manifest = ContentManifest.compute(
            layout.table, [ResourceDescriptor.from_resource(r, layout.table) for r in inputs])
        diamond, analyze, generated = realization.plan.passes

        segment = SegmentDescriptor.from_realization(
            realization, [diamond, generated], inputs, goals, layout.table, manifest)
        received = SegmentDescriptor.from_json(wire(segment.to_json()))

        assert received.project_name == "proj"
        assert received.project_config == {"name": "proj"}
        assert received.filter_vars == {"vendor": "lattice", "tool": "diamond"}
        assert received.inputs == segment.inputs
        assert received.goals == segment.goals
        assert received.manifest.entries == manifest.entries

        passes = received.passes_instantiate(realization.backends, gbs_config=None)
        assert [pm.name for pm in passes] == ["diamond-ecp5", "gen-constraints"]
        assert type(passes[0].pass_obj) is type(diamond.pass_obj)
        assert vars(passes[0].pass_obj.part) == vars(diamond.pass_obj.part)
        assert passes[0].pass_obj.vhdl_std == "2008"
        assert passes[0].backend_name == "gbs.builtin.diamond"
        assert passes[1].requested_types == {"bitstream"}

        base = tmp_path / "remote"
        placed = received.roots.placed(base)
        plan = received.plan(realization.backends, None, placed)
        og = plan.output_group
        assert [pm.name for pm in plan.passes] == ["diamond-ecp5", "gen-constraints"]
        assert og.name == "synth" and og.topcell == "top"
        assert og.target == {"part": "LFE5U-25F-6BG256C"}
        assert og.exclude_dispatchers == ["validation-report"]
        assert og.outputs[0].path == base.resolve() / "roots" / "project" / "top.bit"
        assert received.output_group.topcell_library == "work"
        assert received.project_model(placed).name == "proj"

        remote_ctx = BuildContext(base_output_path=placed.path_of(
            RootedPath("output", PurePosixPath("."))))
        remote_inputs, remote_goals = received.resources_get(remote_ctx, placed)
        assert remote_inputs[0].path == base.resolve() / "roots" / "project" / "lib" / "repo" / "top.vhd"
        assert remote_inputs[0].library == "work"
        assert remote_goals[0].typology == ResourceTypology.OUTPUT

    async def test_missing_backend_is_an_error(self, layout, realization):
        segment = SegmentDescriptor.from_realization(
            realization, realization.plan.passes[:1], [], [], layout.table)
        received = SegmentDescriptor.from_json(wire(segment.to_json()))
        with pytest.raises(WireError, match="not available"):
            received.passes_instantiate([], None)

    async def test_foreign_pass_is_refused(self, layout, realization):
        other = self.pass_metadata(RequestTriggeredBackend(), {}, {"bitstream"})
        with pytest.raises(WireError, match="not part of the plan"):
            SegmentDescriptor.from_realization(realization, other, [], [], layout.table)

    async def test_non_json_config_is_refused(self, layout, realization):
        realization.plan.passes[0].config["script"] = Path("/x.tcl")
        with pytest.raises(WireError, match="not plain JSON"):
            SegmentDescriptor.from_realization(
                realization, realization.plan.passes[:1], [], [], layout.table)

    async def test_strict_reading(self, layout, realization):
        data = wire(SegmentDescriptor.from_realization(
            realization, realization.plan.passes[:1], [], [], layout.table).to_json())
        with pytest.raises(WireError, match="version"):
            SegmentDescriptor.from_json(dict(data, version=0))
        with pytest.raises(WireError, match="unknown field"):
            SegmentDescriptor.from_json(dict(data, extra=None))
        bad = wire(data)
        bad["goals"] = [{"location": {"root": "nowhere", "path": "x"}, "file_type": None,
                         "file_type_aliases": [], "file_type_version": None, "library": None,
                         "typology": "output", "generated_by": None, "directory": False,
                         "metadata": {}}]
        with pytest.raises(WireError, match="unknown root"):
            SegmentDescriptor.from_json(bad)


def test_planner_records_requested_types():
    planner = BuildPlanner([FakeRepository("r", None, {"netlist"})], [RequestTriggeredBackend()])
    og = OutputGroup(name="og", topcell="top", outputs=[OutputFile("bitstream", Path("b"))])

    pm, = planner._query_backends(og, {"bitstream"})

    assert pm.name == "gen-constraints"
    assert "bitstream" in pm.requested_types
