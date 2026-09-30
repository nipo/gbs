"""Test plugin providing a generic dispatcher

The dispatcher, rt-extra, only leaves a MARKER file in the home
directory of the host it runs on: tests install this plugin on one
host only, to check plugin compatibility with the other. Only
subprocesses see this plugin: tests put this directory on their
PYTHONPATH.
"""

from pathlib import Path

from gbs.base import BaseDispatcher, BasePlugin

MARKER = "rt-extra-ran"


class ExtraDispatcher(BaseDispatcher):
    def __init__(self, context):
        super().__init__(context, "rt-extra", tool_name="rtextra")

    async def process(self):
        (Path.home() / MARKER).touch()


class RemoteExtraPlugin(BasePlugin):
    def __init__(self):
        super().__init__(name="gbs.plugin.remoteextra",
                         description="Generic dispatcher test plugin", version="0.0.1")

    def generic_dispatchers(self, context):
        return [ExtraDispatcher(context)]


def gbs_register():
    return RemoteExtraPlugin()
