"""Tests for the NVC backend task graph"""

from types import SimpleNamespace

import pytest

from gbs.build import BuildContext
from gbs.builtin.nvc.dispatcher import NVCDispatcher
from gbs.repository.model import SourceFile


@pytest.mark.asyncio
async def test_library_directories_are_declared(tmp_path):
    """NVC reads design units from whole library directories"""
    ctx = BuildContext(project=SimpleNamespace(name="p", root_library_name="work"),
                       base_output_path=tmp_path / "build")
    ctx.set_output_group_context(topcell="top", topcell_library="work",
                                 output_group=SimpleNamespace(name="sim"))
    sources = {}
    for library in ("dep", "work"):
        path = tmp_path / f"{library}.vhd"
        path.write_text("")
        sources[f"{library}.p"] = [SourceFile(path=path, file_type="vhdl")]
    ctx.populate_pending(SimpleNamespace(
        partitions=list(sources),
        sources=sources,
        partition_deps={"work.p": {"dep.p"}},
    ), {"vhdl"})

    dispatcher = NVCDispatcher(ctx)
    await dispatcher.process()
    await dispatcher.process()

    _, dep_task = dispatcher.library_build_get("dep")
    _, work_task = dispatcher.library_build_get("work")
    dep_dir, = dep_task.outputs_of_type("nvc-lib-dir")
    work_dir, = work_task.outputs_of_type("nvc-lib-dir")

    assert dep_dir.directory
    assert dep_dir.path == dispatcher.library_workdir("dep").resolve()
    assert work_task.inputs_of_type("nvc-lib-dir") == [dep_dir]
    assert set(dispatcher._linker.inputs_of_type("nvc-lib-dir")) == {dep_dir, work_dir}
