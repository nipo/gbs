"""Test plugin for remote segment execution

Three passes, each needing its own tool, so a test chooses which host
runs which by configuring tools:

- rt-prep (tool rtprep): rt-raw sources -> rt-src files, one per
  dispatch round, so its outputs arrive over several rounds.
- rt-gen (tool rtgen): rt-src -> rt-mid file, rt-tree directory and
  rt-report. The report names the home directory it ran with. A source
  holding FAIL makes it fail with an error message; one holding
  "HANG <path>" makes it write <path>, wait forever, and write
  <path>.cancelled when cancelled.
- rt-use (tool rtuse): rt-mid and rt-tree -> rt-out.

Only subprocesses see this plugin: tests put this directory on their
PYTHONPATH.
"""

from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any

from gbs.base import BaseBackend, BaseDispatcher, BasePass, BasePlugin
from gbs.build.task import BuildError, ResourceTypology, Task
from gbs.ui.messages import MessageSeverity


class PrepTask(Task):
    async def work(self):
        source, = self.inputs
        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(source.path.read_text())


class GenTask(Task):
    async def work(self):
        text = "".join(r.path.read_text() for r in sorted(self.inputs_of_type("rt-src"),
                                                           key=lambda r: r.path.name))
        if "FAIL" in text:
            await self.add_message(MessageSeverity.ERROR, "generator refuses FAIL",
                                   file_path=self.inputs_of_type("rt-src")[0].path, line=1)
            raise BuildError("generation failed")
        for line in text.splitlines():
            if line.startswith("HANG "):
                marker = Path(line.split(" ", 1)[1])
                marker.write_text("started")
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    Path(f"{marker}.cancelled").write_text("cancelled")
                    raise
        mid, = self.outputs_of_type("rt-mid")
        tree, = self.outputs_of_type("rt-tree")
        report, = self.outputs_of_type("rt-report")
        mid.path.parent.mkdir(parents=True, exist_ok=True)
        mid.path.write_text(text.upper())
        tree.path.mkdir(parents=True, exist_ok=True)
        for index, line in enumerate(text.splitlines()):
            sub = tree.path / f"d{index}"
            sub.mkdir(exist_ok=True)
            (sub / f"{line.strip() or 'blank'}.txt").write_text(line)
        (tree.path / "empty").mkdir(exist_ok=True)
        tool = tree.path / "tool.sh"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
        report.path.write_text(f"home {Path.home()}\n")


class UseTask(Task):
    async def work(self):
        mid, = self.inputs_of_type("rt-mid")
        tree, = self.inputs_of_type("rt-tree")
        out, = self.outputs
        listing = sorted(p.relative_to(tree.path).as_posix() for p in tree.path.rglob("*"))
        out.path.parent.mkdir(parents=True, exist_ok=True)
        out.path.write_text(mid.path.read_text() + "\n".join(listing) + "\n")


class PrepDispatcher(BaseDispatcher):
    def __init__(self, context):
        super().__init__(context, "rt-prep", tool_name="rtprep")

    async def process(self):
        raws = self.context.filter_pending(file_type="rt-raw")
        if not raws:
            return
        raw = raws[0]
        src = self.context.get_resource(
            self.context.output_path / "src" / f"{raw.path.stem}.src",
            file_type="rt-src", typology=ResourceTypology.INTERMEDIATE, generated_by=self.name)
        PrepTask(self, f"rt-prep-{raw.path.stem}", inputs=[raw], outputs=[src])


class GenDispatcher(BaseDispatcher):
    def __init__(self, context):
        super().__init__(context, "rt-gen", tool_name="rtgen")
        self.task = None

    async def process(self):
        sources = self.context.filter_pending(file_type="rt-src")
        if self.task is not None or not sources:
            return
        out = self.context.output_path
        outputs = [
            self.context.get_resource(out / "mid.txt", file_type="rt-mid",
                                      typology=ResourceTypology.INTERMEDIATE, generated_by=self.name),
            self.context.get_resource(out / "tree", file_type="rt-tree", directory=True,
                                      typology=ResourceTypology.INTERMEDIATE, generated_by=self.name),
            self.context.get_resource(out / "report.txt", file_type="rt-report",
                                      typology=ResourceTypology.INTERMEDIATE, generated_by=self.name),
        ]
        self.task = GenTask(self, "rt-gen", inputs=sources, outputs=outputs)
        for source in sources:
            self.context.remove_pending(source.path)


class UseDispatcher(BaseDispatcher):
    def __init__(self, context):
        super().__init__(context, "rt-use", tool_name="rtuse")
        self.task = None

    async def process(self):
        mids = self.context.filter_pending(file_type="rt-mid")
        trees = self.context.filter_pending(file_type="rt-tree")
        if self.task is not None or not mids or not trees:
            return
        out = self.context.get_resource(
            self.context.output_path / "out.txt", file_type="rt-out",
            typology=ResourceTypology.INTERMEDIATE, generated_by=self.name)
        self.task = UseTask(self, "rt-use", inputs=mids + trees, outputs=[out])


class ToolPass(BasePass):
    tool = None
    dispatcher_class = None
    types_with_library = set()

    def probe(self):
        return self.probe_tool(self.tool)

    def dispatchers(self, context):
        return [self.dispatcher_class(context)]


class PrepPass(ToolPass):
    name = "rt-prep"
    tool = "rtprep"
    dispatcher_class = PrepDispatcher
    input_types = {"rt-raw"}
    output_types = {"rt-src"}


class GenPass(ToolPass):
    name = "rt-gen"
    tool = "rtgen"
    dispatcher_class = GenDispatcher
    input_types = {"rt-src"}
    output_types = {"rt-mid", "rt-tree", "rt-report"}


class UsePass(ToolPass):
    name = "rt-use"
    tool = "rtuse"
    dispatcher_class = UseDispatcher
    input_types = {"rt-mid", "rt-tree"}
    output_types = {"rt-out"}


class RemoteTestBackend(BaseBackend):
    PASSES = (PrepPass, GenPass, UsePass)

    def __init__(self):
        super().__init__("gbs.plugin.remotetest")

    def contribute_passes(self, config: dict[str, Any], output_types: set[str],
                          project_config=None, gbs_config=None):
        return [cls(config, project_config, gbs_config)
                for cls in self.PASSES if cls.output_types & output_types]


class RemoteTestPlugin(BasePlugin):
    def __init__(self):
        super().__init__(name="gbs.plugin.remotetest",
                         description="Remote execution test passes", version="0.0.1")

    def enumerate_backends(self):
        return [RemoteTestBackend()]


def gbs_register():
    return RemoteTestPlugin()
