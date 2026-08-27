"""Tests for output inventories (`gbs project outputs`, `gbs suite outputs`)"""

from collections import namedtuple
from pathlib import Path

import pytest
import yaml

from gbs.config.model import GBSConfig
from gbs.planner.planner import BuildPlanner, PlanningError
from gbs.project import Project
from gbs.project.output_inventory import OutputInventory
from gbs.suite import ExecutionError, load_suite
from gbs.suite.output_inventory import SuiteOutputInventory


FakePass = namedtuple("FakePass", ["backend_name"])
FakePlan = namedtuple("FakePlan", ["passes"])


@pytest.fixture
def planned(monkeypatch):
    """Make every plan succeed, naming one backend.

    Planning depends on which toolchains this machine has; the inventory
    records are what is under test, not the search itself.
    """
    def plan(self, output_group):
        return FakePlan(passes=[FakePass(backend_name="gbs.builtin.fake")])

    monkeypatch.setattr(BuildPlanner, "plan", plan)


def write_project(directory: Path, name: str, roots, output) -> Path:
    """Write a project file with `roots` root partition(s) and `output` groups."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "top.vhd").write_text("-- empty\n")

    project_file = directory / "project.gbs.yaml"
    project_file.write_text(yaml.dump({
        "name": name,
        "root": roots,
        "output": output,
    }))
    return project_file


def simple_root(name: str = "top") -> dict:
    return {
        "name": name,
        "sources": [{"file_type": "vhdl", "files": ["top.vhd"]}],
    }


def load(project_file: Path) -> Project:
    return Project.load_from_file(project_file, gbs_config=GBSConfig())


class TestOutputInventory:
    """Records built for a single project"""

    def test_record_keys(self, tmp_path, planned):
        project_file = write_project(tmp_path / "proj", "myproj", simple_root(), [{
            "name": "synthesis",
            "topcell": "top",
            "target": {"part": "iCE40UP5K-SG48I"},
            "outputs": [{"type": "bitstream", "path": "blink.bin"}],
        }])

        records = OutputInventory(load(project_file), name="myproj").records()

        assert records == [{
            "project": "myproj",
            "group": "synthesis",
            "topcell": "top",
            "part": "iCE40UP5K-SG48I",
            "backends": ["gbs.builtin.fake"],
            "outputs": [{
                "type": "bitstream",
                "path": str(tmp_path / "proj" / "blink.bin"),
            }],
        }]

    def test_absent_keys_omitted(self, tmp_path, planned):
        project_file = write_project(tmp_path / "proj", "myproj", simple_root(), [{
            "name": "simulation",
            "topcell": "top",
            "outputs": [{"type": "simulator", "path": "sim"}],
        }])

        record = OutputInventory(load(project_file), name="myproj").records()[0]

        assert "part" not in record
        assert "partition" not in record
        assert "error" not in record

    def test_partition_reported(self, tmp_path, planned):
        project_file = write_project(
            tmp_path / "proj", "myproj",
            [simple_root("first"), simple_root("second")],
            [{
                "name": "simulation",
                "topcell": "top",
                "partition": "second",
                "outputs": [{"type": "simulator", "path": "sim"}],
            }],
        )

        record = OutputInventory(load(project_file), name="myproj").records()[0]

        assert record["partition"] == "second"

    def test_group_order_follows_project_file(self, tmp_path, planned):
        project_file = write_project(tmp_path / "proj", "myproj", simple_root(), [
            {"name": "second", "topcell": "top", "outputs": []},
            {"name": "first", "topcell": "top", "outputs": []},
        ])

        records = OutputInventory(load(project_file), name="myproj").records()

        assert [r["group"] for r in records] == ["second", "first"]

    def test_group_names_select_a_subset(self, tmp_path, planned):
        project_file = write_project(tmp_path / "proj", "myproj", simple_root(), [
            {"name": "simulation", "topcell": "top", "outputs": []},
            {"name": "synthesis", "topcell": "top", "outputs": []},
        ])

        inventory = OutputInventory(
            load(project_file), name="myproj", group_names=["synthesis"]
        )

        assert [r["group"] for r in inventory.records()] == ["synthesis"]

    def test_unknown_group_name_rejected(self, tmp_path, planned):
        project_file = write_project(tmp_path / "proj", "myproj", simple_root(), [
            {"name": "simulation", "topcell": "top", "outputs": []},
        ])

        inventory = OutputInventory(
            load(project_file), name="myproj", group_names=["nope"]
        )

        with pytest.raises(ValueError, match="nope"):
            inventory.records()

    def test_planning_failure_becomes_a_record(self, tmp_path, monkeypatch):
        """An output group no backend can reach is described, not raised.

        The suite case this exists for is a project whose vendor tool is
        missing from this machine.
        """
        def plan(self, output_group):
            if output_group.name == "unreachable":
                raise PlanningError(
                    "Cannot find passes from ['vhdl'] to ['nothing'].\n"
                    "Considered chains:\n  - nothing"
                )
            return FakePlan(passes=[FakePass(backend_name="gbs.builtin.fake")])

        monkeypatch.setattr(BuildPlanner, "plan", plan)

        project_file = write_project(tmp_path / "proj", "myproj", simple_root(), [
            {"name": "unreachable", "topcell": "top",
             "outputs": [{"type": "nothing", "path": "out.bin"}]},
            {"name": "reachable", "topcell": "top", "outputs": []},
        ])

        failed, ok = OutputInventory(load(project_file), name="myproj").records()

        # Only the headline of the diagnostic; the rest goes to the log
        assert failed["error"] == "Cannot find passes from ['vhdl'] to ['nothing']."
        assert "backends" not in failed
        assert failed["outputs"] == [{
            "type": "nothing",
            "path": str(tmp_path / "proj" / "out.bin"),
        }]
        # The failure of one group does not hide the others
        assert ok["backends"] == ["gbs.builtin.fake"]


class TestSuiteOutputInventory:
    """Records built for a whole suite"""

    @pytest.fixture
    def suite_dir(self, tmp_path):
        """A suite of three tagged projects, one of them skipped."""
        for name in ("alpha", "beta", "gamma"):
            write_project(tmp_path / name, name, simple_root(), [
                {"name": "simulation", "topcell": "top",
                 "outputs": [{"type": "simulator", "path": "sim"}]},
                {"name": "synthesis", "topcell": "top",
                 "outputs": [{"type": "bitstream", "path": "out.bin"}]},
            ])

        suite_file = tmp_path / "suite.gbs.yaml"
        suite_file.write_text(yaml.dump({
            "name": "test-suite",
            "projects": [
                {"name": "alpha-entry", "path": "alpha", "tags": ["sim"]},
                {"name": "beta-entry", "path": "beta", "tags": ["synth"],
                 "output_groups": ["synthesis"]},
                {"name": "gamma-entry", "path": "gamma", "tags": ["sim"],
                 "skip": True},
            ],
        }))
        return tmp_path

    def _inventory(self, suite_dir, **kwargs):
        return SuiteOutputInventory(
            load_suite(suite_dir / "suite.gbs.yaml"),
            gbs_config=GBSConfig(),
            **kwargs,
        )

    def test_records_carry_the_suite_local_name(self, suite_dir, planned):
        records = self._inventory(suite_dir).records()

        # The suite entry name, not the project's own 'alpha'
        assert {r["project"] for r in records} == {"alpha-entry", "beta-entry"}

    def test_skipped_project_omitted(self, suite_dir, planned):
        records = self._inventory(suite_dir).records()

        assert all(r["project"] != "gamma-entry" for r in records)

    def test_project_output_groups_honored(self, suite_dir, planned):
        records = self._inventory(suite_dir).records()

        beta = [r["group"] for r in records if r["project"] == "beta-entry"]
        alpha = [r["group"] for r in records if r["project"] == "alpha-entry"]
        assert beta == ["synthesis"]
        assert alpha == ["simulation", "synthesis"]

    def test_tag_filter(self, suite_dir, planned):
        records = self._inventory(suite_dir, tags=["synth"]).records()

        assert {r["project"] for r in records} == {"beta-entry"}

    def test_exclude_tag_filter(self, suite_dir, planned):
        records = self._inventory(suite_dir, exclude_tags=["synth"]).records()

        assert {r["project"] for r in records} == {"alpha-entry"}

    def test_schema_matches_project_command(self, suite_dir, planned):
        """A suite record and a project record are the same shape."""
        suite_record = next(
            r for r in self._inventory(suite_dir).records()
            if r["project"] == "alpha-entry"
        )
        project_record = OutputInventory(
            load(suite_dir / "alpha" / "project.gbs.yaml"), name="alpha"
        ).records()[0]

        assert list(suite_record) == list(project_record)
        assert suite_record["outputs"] == project_record["outputs"]

    def test_missing_project_file_is_an_error(self, tmp_path, planned):
        """A broken suite reference is a suite file bug, not a record."""
        suite_file = tmp_path / "suite.gbs.yaml"
        suite_file.write_text(yaml.dump({
            "name": "test-suite",
            "projects": [{"name": "ghost", "path": "nowhere"}],
        }))

        inventory = SuiteOutputInventory(
            load_suite(suite_file), gbs_config=GBSConfig()
        )

        with pytest.raises(ExecutionError, match="ghost"):
            inventory.records()
