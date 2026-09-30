"""Tests for directory resources: a whole directory tree as a task input or output"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from gbs.build import BuildContext, BuildError, Resource, Task
from gbs.build.task import ResourceTypology
from gbs.repository.model import SourceFile


class MockDispatcher:
    def __init__(self, context):
        self.context = context
        self.name = "mock"


class NullTask(Task):
    async def work(self) -> None:
        pass


def set_mtime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def tree(root: Path, mtime: float) -> Path:
    """A small tree with every entry at the same modification time"""
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("a")
    (root / "sub" / "b.txt").write_text("b")
    for path in (root / "sub" / "b.txt", root / "a.txt", root / "sub", root):
        set_mtime(path, mtime)
    return root


class TestTreeMtime:
    def test_newest_nested_entry_wins(self, tmp_path):
        root = tree(tmp_path / "d", 1000)
        set_mtime(root / "sub" / "b.txt", 2000)

        assert Resource.tree_mtime_get(root) == 2000

    def test_new_entry_counts_as_change(self, tmp_path):
        root = tree(tmp_path / "d", 1000)
        (root / "sub" / "c.txt").write_text("c")
        set_mtime(root / "sub" / "c.txt", 1000)

        # The file itself is old, but adding it touched its directory
        assert Resource.tree_mtime_get(root) > 1000

    def test_removed_entry_counts_as_change(self, tmp_path):
        root = tree(tmp_path / "d", 1000)
        (root / "sub" / "b.txt").unlink()

        assert Resource.tree_mtime_get(root) > 1000

    def test_missing_directory(self, tmp_path):
        assert Resource.tree_mtime_get(tmp_path / "missing") is None

    def test_file_is_not_a_tree(self, tmp_path):
        f = tmp_path / "f"
        f.write_text("")

        assert Resource.tree_mtime_get(f) is None


class TestDirectoryResource:
    @pytest.mark.asyncio
    async def test_directory_flag_defaults_to_file(self, tmp_path):
        ctx = BuildContext()

        assert not ctx.get_resource(tmp_path / "f").directory

    @pytest.mark.asyncio
    async def test_directory_flag_is_updated_on_the_singleton(self, tmp_path):
        ctx = BuildContext()
        r = ctx.get_resource(tmp_path / "d", file_type="some-dir")

        assert ctx.get_resource(tmp_path / "d", directory=True) is r
        assert r.directory
        # Not specifying the flag leaves it alone
        ctx.get_resource(tmp_path / "d", file_type="some-dir")
        assert r.directory

    @pytest.mark.asyncio
    async def test_directory_mtime_is_the_tree_mtime(self, tmp_path):
        ctx = BuildContext()
        root = tree(tmp_path / "d", 1000)
        set_mtime(root / "sub" / "b.txt", 2000)

        assert ctx.get_resource(root, directory=True).mtime_get() == 2000

    @pytest.mark.asyncio
    async def test_directory_exists_only_as_a_directory(self, tmp_path):
        ctx = BuildContext()
        (tmp_path / "f").write_text("")
        (tmp_path / "d").mkdir()

        assert not ctx.get_resource(tmp_path / "f", directory=True).exists()
        assert ctx.get_resource(tmp_path / "d", directory=True).exists()

    @pytest.mark.asyncio
    async def test_missing_directory_input_fails(self, tmp_path):
        ctx = BuildContext()
        r = ctx.get_resource(tmp_path / "d", directory=True)

        async with ctx.build():
            with pytest.raises(BuildError, match="Directory .* missing"):
                await r


class TestDirectoryInputRebuild:
    def graph(self, tmp_path):
        ctx = BuildContext()
        root = tree(tmp_path / "d", 1000)
        out = tmp_path / "out"
        out.write_text("")
        set_mtime(out, 1500)
        task = NullTask(
            MockDispatcher(ctx), "t",
            inputs=[ctx.get_resource(root, directory=True)],
            outputs=[ctx.get_resource(out)],
        )
        return root, task

    @pytest.mark.asyncio
    async def test_up_to_date_when_output_newer_than_tree(self, tmp_path):
        _, task = self.graph(tmp_path)

        assert not task.is_rebuild_needed()

    @pytest.mark.asyncio
    async def test_nested_change_triggers_rebuild(self, tmp_path):
        root, task = self.graph(tmp_path)
        set_mtime(root / "sub" / "b.txt", 2000)

        assert task.is_rebuild_needed()

    @pytest.mark.asyncio
    async def test_directory_output_is_up_to_date_by_its_tree(self, tmp_path):
        ctx = BuildContext()
        src = tmp_path / "src"
        src.write_text("")
        set_mtime(src, 1500)
        out = tree(tmp_path / "out", 1000)
        task = NullTask(
            MockDispatcher(ctx), "t",
            inputs=[ctx.get_resource(src)],
            outputs=[ctx.get_resource(out, directory=True)],
        )

        assert task.is_rebuild_needed()
        set_mtime(out / "sub" / "b.txt", 2000)
        assert not task.is_rebuild_needed()


class TestPopulatePending:
    @pytest.mark.asyncio
    async def test_directory_sources_are_directory_resources(self, tmp_path):
        ctx = BuildContext(base_output_path=tmp_path / "build")
        repo = tmp_path / "ip_repo"
        repo.mkdir()
        hdl = tmp_path / "top.vhd"
        hdl.write_text("")

        build_set = SimpleNamespace(
            partitions=["work.top"],
            sources={"work.top": [
                SourceFile(path=hdl, file_type="vhdl"),
                SourceFile(path=repo, file_type="vivado-ip-repository"),
            ]},
            partition_deps={},
        )
        ctx.populate_pending(build_set, {"vhdl"})

        assert ctx.get_resource(repo).directory
        assert not ctx.get_resource(hdl).directory
        assert ctx.get_pending(repo).typology == ResourceTypology.SOURCE
