"""Backend precedence: configuration, CLI and the planner's choice
between plans that remain viable"""

from pathlib import Path

import pytest
from asyncclick.testing import CliRunner

from gbs.cli import cli
from gbs.config.model import ConfigError, GBSConfig
from gbs.planner.planner import BuildPlanner, PlanningError
from gbs.project.model import OutputFile, OutputGroup
from gbs.repository.model import Repository


class FakeRepository(Repository):
    def __init__(self):
        super().__init__("fake", None)

    def file_types(self):
        return {"src"}

    def partition_lookup(self, partition_name, filter_vars):
        return None


class FakePass:
    types_with_library = set()

    def filter_vars(self):
        return {}

    def probe(self):
        return None

    @staticmethod
    def make(name, input_types, output_types):
        return type(name, (FakePass,), dict(
            name=name,
            input_types=set(input_types),
            output_types=set(output_types),
        ))()


class FakeBackend:
    def __init__(self, name, passes):
        self.name = name
        self.passes = passes

    def contribute_passes(self, config, output_types, project_config=None, gbs_config=None):
        return [p for p in self.passes if p.output_types & output_types]


class Planning:
    @staticmethod
    def simulators():
        return [
            FakeBackend("gbs.builtin.alpha", [FakePass.make("alpha-sim", {"src"}, {"log"})]),
            FakeBackend("gbs.builtin.beta", [FakePass.make("beta-sim", {"src"}, {"log"})]),
        ]

    @staticmethod
    async def plan(backends, precedence):
        planner = BuildPlanner([FakeRepository()], backends,
                               gbs_config=GBSConfig(backend_precedence=precedence))
        og = OutputGroup(name="og", topcell="top",
                         outputs=[OutputFile(type="log", path=Path("log"))])
        return [p.name for p in (await planner.plan(og)).passes]


class TestPrecedenceOf:
    def test_default_is_zero(self):
        assert GBSConfig().precedence_of("gbs.builtin.yavs") == 0

    def test_last_matching_entry_wins(self):
        config = GBSConfig(backend_precedence=[
            ("yavs", 10), ("ghdl", 5), ("gbs.builtin.yavs", -1)])
        assert config.precedence_of("gbs.builtin.yavs") == -1
        assert config.precedence_of("gbs.builtin.ghdl") == 5


class TestConfigFile:
    def test_parse_and_merge(self, tmp_path):
        user = tmp_path / "user.yaml"
        user.write_text("backend_precedence:\n  yavs: 10\n  ghdl: 5\n")
        tree = tmp_path / "tree.yaml"
        tree.write_text("backend_precedence:\n  yavs: 1\n")

        merged = GBSConfig._merge_configs(
            GBSConfig._parse_config_file(user), GBSConfig._parse_config_file(tree))

        assert merged.backend_precedence == [("yavs", 10), ("ghdl", 5), ("yavs", 1)]
        assert merged.precedence_of("gbs.builtin.yavs") == 1

    @pytest.mark.parametrize("content, message", [
        ("backend_precedence: [yavs]\n", "must be a mapping"),
        ("backend_precedence:\n  yavs: high\n", "must be an integer"),
        ("backend_precedence:\n  yavs: true\n", "must be an integer"),
    ])
    def test_invalid(self, tmp_path, content, message):
        path = tmp_path / "gbs.yaml"
        path.write_text(content)
        with pytest.raises(ConfigError, match=message):
            GBSConfig._parse_config_file(path)


class TestPlanner:
    async def test_tie_is_an_error(self):
        with pytest.raises(PlanningError, match="2 viable plans remain"):
            await Planning.plan(Planning.simulators(), [])

    async def test_equal_precedence_is_an_error(self):
        with pytest.raises(PlanningError, match=r"gbs.builtin.alpha=3"):
            await Planning.plan(Planning.simulators(), [("alpha", 3), ("beta", 3)])

    @pytest.mark.parametrize("precedence, chosen", [
        ([("alpha", 1)], ["alpha-sim"]),
        ([("beta", 1)], ["beta-sim"]),
        ([("alpha", -1)], ["beta-sim"]),
    ])
    async def test_highest_precedence_wins(self, precedence, chosen):
        assert await Planning.plan(Planning.simulators(), precedence) == chosen

    async def test_shared_backends_are_not_compared(self):
        front = FakeBackend("gbs.builtin.front", [
            FakePass.make("front", {"src"}, {"mid"})])
        back = [
            FakeBackend("gbs.builtin.alpha", [FakePass.make("alpha-sim", {"mid"}, {"log"})]),
            FakeBackend("gbs.builtin.beta", [FakePass.make("beta-sim", {"mid"}, {"log"})]),
        ]
        names = await Planning.plan([front] + back, [("front", 100), ("beta", 1)])
        assert sorted(names) == ["beta-sim", "front"]

    async def test_plan_of_shared_backends_only_is_not_decided(self):
        alpha = FakeBackend("gbs.builtin.alpha", [
            FakePass.make("alpha-sim", {"src"}, {"log"}),
            FakePass.make("alpha-other-sim", {"src"}, {"log"}),
        ])
        with pytest.raises(PlanningError, match="2 viable plans remain"):
            await Planning.plan([alpha], [("alpha", 1)])


class TestCli:
    async def test_precedence_is_recorded_after_config(self):
        result = await CliRunner().invoke(
            cli, ["--precedence", "yavs=10", "config", "dump"])
        assert result.exit_code == 0, result.output
        assert "  - yavs: 10" in result.output

    async def test_precedence_rejects_unmatched_backend(self):
        result = await CliRunner().invoke(
            cli, ["--precedence", "nosuch=1", "config", "dump"])
        assert result.exit_code != 0
        assert "--precedence nosuch=1" in result.output

    async def test_precedence_rejects_non_integer(self):
        result = await CliRunner().invoke(
            cli, ["--precedence", "yavs=high", "config", "dump"])
        assert result.exit_code != 0
        assert "PRECEDENCE an integer" in result.output
