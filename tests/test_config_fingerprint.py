"""Tests for the config fingerprint of a planned build."""

from pathlib import Path

from gbs.config.model import GBSConfig
from gbs.planner.planner import BuildPlanner
from gbs.project.model import OutputFile, OutputGroup
from gbs.project.project import PlanRealization

from .test_planner_search import FakeBackend, FakePass, FakeRepository


class TestConfigFingerprint:
    @staticmethod
    async def fingerprint(gbs_config):
        planner = BuildPlanner(
            [FakeRepository({"s"})],
            [FakeBackend([FakePass.make("make-a", {"s"}, {"a"})])],
            gbs_config=gbs_config,
        )
        og = OutputGroup(
            name="og",
            topcell="top",
            filter_vars={},
            backend_config={"fake": {"tool": "fake:base"}},
            outputs=[OutputFile(type="a", path=Path("a"))],
        )
        plan = await planner.plan(og)
        return PlanRealization.fingerprint_data_build(plan, gbs_config)

    async def test_tool_override_changes_fingerprint(self):
        plain = await self.fingerprint(GBSConfig())
        overridden = await self.fingerprint(
            GBSConfig(tool_overrides=[("fake", "fake:other")]))

        assert plain["backend_config"] == {"fake": {"tool": "fake:base"}}
        assert overridden["backend_config"] == {"fake": {"tool": "fake:other"}}

    async def test_tool_version_override_changes_fingerprint(self):
        plain = await self.fingerprint(GBSConfig())
        pinned = await self.fingerprint(
            GBSConfig(tool_version_overrides=[("fake", "1.2")]))

        assert pinned["backend_config"] == {
            "fake": {"tool": "fake:base", "tool_version": "1.2"}}
        assert pinned != plain
