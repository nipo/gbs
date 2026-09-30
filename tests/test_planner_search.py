"""Tests for the planner chain search over fake passes."""

from pathlib import Path

import pytest

from gbs.planner.planner import BuildPlanner, PlanningError
from gbs.project.model import OutputFile, OutputGroup
from gbs.repository.model import Repository


class FakeRepository(Repository):
    def __init__(self, file_types):
        super().__init__("fake", None)
        self.types = set(file_types)

    def file_types(self):
        return self.types

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
        """Pass metadata compares passes by class, so each fake pass
        gets its own."""
        return type(name, (FakePass,), dict(
            name=name,
            input_types=set(input_types),
            output_types=set(output_types),
        ))()


class FakeBackend:
    name = "fake"

    def __init__(self, passes):
        self.passes = passes

    def contribute_passes(self, config, output_types, project_config=None, gbs_config=None):
        return [p for p in self.passes if p.output_types & output_types]


class TestPlannerSearch:
    @staticmethod
    def plan(source_types, passes, output_types, **kwargs):
        planner = BuildPlanner(
            [FakeRepository(source_types)],
            [FakeBackend(passes)],
            **kwargs,
        )
        og = OutputGroup(
            name="og",
            topcell="top",
            filter_vars={},
            backend_config={},
            outputs=[OutputFile(type=t, path=Path(t)) for t in sorted(output_types)],
        )
        return [p.name for p in planner.plan(og).passes]

    def test_unrelated_producers_both_planned(self):
        passes = [FakePass.make("make-a", {"s"}, {"a"}),
                  FakePass.make("make-b", {"s"}, {"b"})]

        names = self.plan({"s"}, passes, {"a", "b"})

        assert sorted(names) == ["make-a", "make-b"]

    def test_producers_sharing_an_intermediate(self):
        passes = [FakePass.make("make-x", {"s"}, {"x"}),
                  FakePass.make("make-a", {"x"}, {"a"}),
                  FakePass.make("make-b", {"x"}, {"b"})]

        names = self.plan({"s"}, passes, {"a", "b"})

        assert sorted(names) == ["make-a", "make-b", "make-x"]

    def test_output_copied_from_sources(self):
        passes = [FakePass.make("make-a", {"s"}, {"a"})]

        names = self.plan({"s"}, passes, {"a", "s"})

        assert names == ["make-a"]

    def test_missing_producer_fails(self):
        passes = [FakePass.make("make-a", {"s"}, {"a"})]

        with pytest.raises(PlanningError):
            self.plan({"s"}, passes, {"a", "b"})

    def test_partial_coverage_needs_every_output(self):
        passes = [FakePass.make("make-a", {"s"}, {"a"}),
                  FakePass.make("make-b", {"s"}, {"b"})]

        names = self.plan({"s", "t"}, passes, {"a", "b"},
                          partial_source_coverage=True)

        assert sorted(names) == ["make-a", "make-b"]
