"""Tests for partition validation (`gbs partition validate`)"""

import shutil
import textwrap
from pathlib import Path

import pytest
import yaml

from gbs.build import BuildContext
from gbs.builtin.ghdl.backend import GHDLBackend
from gbs.builtin.ghdl.passes import GHDLValidatePass
from gbs.cli.partition import ValidateCommand
from gbs.config.model import GBSConfig, ToolConfig
from gbs.planner.planner import BuildPlanner, PlanningError
from gbs.plugins import get_plugin_registry
from gbs.project.model import OutputFile, OutputGroup
from gbs.project.partition_validation import (
    PartitionValidation,
    PartitionValidationError,
)
from gbs.repository.loader import load_repository
from gbs.validation_report import (
    DIAGNOSTICS_FILE_TYPE,
    SKIPPED_FILE_REASON,
    Diagnostic,
    DiagnosticsSidecar,
)

GHDL_PATH = shutil.which("ghdl")
requires_ghdl = pytest.mark.skipif(GHDL_PATH is None, reason="ghdl is not installed")

GHDL_BACKEND = "gbs.builtin.ghdl"


@pytest.fixture(autouse=True)
def cli_process_state():
    """Undo the process-wide state a CLI invocation sets up.

    The command exits through SystemExit, which skips the teardown Click
    would otherwise run at the end of the process:

    - a feedback hub left in place belongs to an event loop that no
      longer exists, and the next test to flush it would wait forever;
    - setup_logging() detaches the "gbs" logger from the root logger,
      which hides its records from any later test reading caplog.
    """
    import logging

    gbs_logger = logging.getLogger("gbs")
    saved = (list(gbs_logger.handlers), gbs_logger.propagate, gbs_logger.level)

    yield

    from gbs.ui import set_global_hub

    set_global_hub(None)
    gbs_logger.handlers, gbs_logger.propagate, gbs_logger.level = saved


class Fixture:
    """Builds a two-library repository on disk for the tests.

    liba.top depends on libb.base, holds one VHDL file and one Verilog
    file no validator reads, so a validation of liba.top exercises the
    dependency tree, the compile order and the skipped file list at once.
    """

    # Analyzes cleanly but with one warning GHDL reports at a known
    # location.
    TOP_WARNING = """\
        entity top is
        end entity;

        architecture rtl of top is
          signal s : bit_vector(3 downto 0);
        begin
          s(4) <= '1';
        end architecture;
        """

    TOP_BROKEN = """\
        entity top is
        end entity;

        architecture rtl of top is
        begin
          bogus <= '1';
        end architecture;
        """

    # An all-sensitized process: rejected before VHDL 2008, accepted from
    # 2008 on, so analyzing it proves which standard GHDL was given.
    TOP_VHDL_2008 = """\
        entity top is
          port (a : in bit; y : out bit);
        end entity;

        architecture rtl of top is
        begin
          process (all) is
          begin
            y <= a;
          end process;
        end architecture;
        """

    BASE = """\
        package base is
          constant answer : integer := 42;
        end package;
        """

    @classmethod
    def write(cls, root: Path, top_source: str = TOP_WARNING,
              with_verilog: bool = True) -> Path:
        """Write the repository under `root`, return its definition file."""
        liba = root / "repo" / "liba"
        libb = root / "repo" / "libb"
        liba.mkdir(parents=True)
        libb.mkdir(parents=True)

        repo_file = root / "repo" / "repo.gbs.yaml"
        repo_file.write_text(
            "name: testrepo\n"
            "libraries:\n"
            "  - path: liba\n"
            "  - path: libb\n"
        )

        (liba / "library.gbs.yaml").write_text(
            "name: liba\npartitions:\n  - top.gbs.yaml\n"
        )
        top_spec = [
            "deps:",
            "  - libb.base",
            "sources:",
            "  - file_type: vhdl",
            "    files:",
            "      - top.vhd",
        ]
        if with_verilog:
            top_spec += [
                "  - file_type: verilog",
                "    files:",
                "      - blob.v",
            ]
            (liba / "blob.v").write_text("module blob; endmodule\n")
        (liba / "top.gbs.yaml").write_text("\n".join(top_spec) + "\n")
        (liba / "top.vhd").write_text(textwrap.dedent(top_source))

        (libb / "library.gbs.yaml").write_text(
            "name: libb\npartitions:\n  - base.gbs.yaml\n"
        )
        (libb / "base.gbs.yaml").write_text(
            "sources:\n  - file_type: vhdl\n    files:\n      - base.vhd\n"
        )
        (libb / "base.vhd").write_text(textwrap.dedent(cls.BASE))

        return repo_file

    @staticmethod
    def gbs_config() -> GBSConfig:
        return GBSConfig(tools=[
            ToolConfig(name="ghdl", variant="test",
                       config={"executable": GHDL_PATH}),
        ])

    @classmethod
    def validation(
        cls,
        tmp_path: Path,
        repo_file: Path | None = None,
        top_source: str = TOP_WARNING,
        partition_name: str = "liba.top",
        filter_vars: dict | None = None,
        backend_config: dict | None = None,
    ) -> PartitionValidation:
        """A validation of liba.top pinned to the GHDL backend.

        Pinning keeps the plan the same whatever backends the machine
        running the tests has plugins for.
        """
        if repo_file is None:
            repo_file = cls.write(tmp_path, top_source=top_source)
        return PartitionValidation(
            partition_name=partition_name,
            repositories=[load_repository(repo_file)],
            gbs_config=cls.gbs_config(),
            report_path=tmp_path / "report.yaml",
            filter_vars=filter_vars or {},
            backend_config={GHDL_BACKEND: backend_config or {}},
            require_backends=[GHDL_BACKEND],
        )

    @staticmethod
    def output_group(path: Path) -> OutputGroup:
        return OutputGroup(
            name="validate",
            topcell="top",
            partition="liba.top",
            outputs=[OutputFile(type="validation-report", path=path)],
            require_backends=[GHDL_BACKEND],
        )


class TestBackendContribution:
    """The GHDL backend offers validation only when it is asked for."""

    def test_contributes_validate_pass(self):
        passes = GHDLBackend().contribute_passes({}, {"validation-report"})

        assert len(passes) == 1
        assert isinstance(passes[0], GHDLValidatePass)

    def test_no_validate_pass_for_other_outputs(self):
        passes = GHDLBackend().contribute_passes({}, {"ghdl-simulator"})

        assert not any(isinstance(p, GHDLValidatePass) for p in passes)

    def test_dispatchers_analyze_then_report(self):
        pass_obj = GHDLValidatePass({"vhdl_standard": "2008"})
        dispatchers = pass_obj.dispatchers(BuildContext())

        assert [d.name for d in dispatchers] == ["ghdl-analyze", "ghdl-validate"]

    def test_config_reaches_the_analyzer(self):
        """-c vhdl_standard=2008 must change the analysis, not just selection."""
        analyze, _ = GHDLValidatePass({"vhdl_standard": "2008"}).dispatchers(
            BuildContext()
        )

        assert analyze.vhdl_std == "2008"
        assert analyze.ghdl_vhdl_version == "08"


@requires_ghdl
class TestPlanning:
    """A validation report is planned to the validation pass alone."""

    def test_planner_selects_validate_pass(self, tmp_path):
        repository = load_repository(Fixture.write(tmp_path))
        planner = BuildPlanner(
            [repository],
            get_plugin_registry().get_all_backends(),
            {},
            Fixture.gbs_config(),
            partial_source_coverage=True,
        )

        plan = planner.plan(Fixture.output_group(tmp_path / "report.yaml"))

        assert [p.name for p in plan.passes] == ["ghdl-validate"]
        assert {p.backend_name for p in plan.passes} == {GHDL_BACKEND}
        assert plan.filter_vars["vhdl_std"] == "1993"

    def test_planner_refuses_partial_coverage_by_default(self, tmp_path):
        """A build must consume every source type; validation need not."""
        repository = load_repository(Fixture.write(tmp_path))
        planner = BuildPlanner(
            [repository],
            get_plugin_registry().get_all_backends(),
            {},
            Fixture.gbs_config(),
        )

        with pytest.raises(PlanningError):
            planner.plan(Fixture.output_group(tmp_path / "report.yaml"))


@requires_ghdl
class TestValidationRun:
    """End-to-end runs against a real GHDL."""

    async def test_report_contents(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        validation = Fixture.validation(tmp_path)

        report = await validation.run()
        data = yaml.safe_load((tmp_path / "report.yaml").read_text())

        assert data["partition"] == "liba.top"
        assert data["status"] == "ok"
        assert report.status == "ok"
        assert data["backends"] == [GHDL_BACKEND]
        assert data["compile_order"] == ["libb.base", "liba.top"]

        tree = {entry["partition"]: entry for entry in data["dependency_tree"]}
        assert tree["liba.top"]["deps"] == ["libb.base"]
        assert tree["libb.base"]["deps"] == []
        assert any(s["path"].endswith("top.vhd") and s["type"] == "vhdl"
                   for s in tree["liba.top"]["sources"])

        assert data["summary"]["warnings"] == 1
        assert data["summary"]["errors"] == 0
        diagnostics, = data["diagnostics"]
        assert diagnostics["file"].endswith("top.vhd")
        message, = diagnostics["messages"]
        assert message["severity"] == "warning"
        assert message["line"] == 7

        # The Verilog file no validator reads is accounted for, not
        # silently dropped.
        skipped, = data["skipped_files"]
        assert skipped["path"].endswith("blob.v")
        assert skipped["type"] == "verilog"
        assert skipped["reason"] == SKIPPED_FILE_REASON

        # Validation stops at analysis.
        assert not (tmp_path / "gbs-build" / "validate" / "elab").exists()
        assert not list(tmp_path.glob("gbs-build/**/simulator*"))

    async def test_command_line_filter_vars_win(self, tmp_path, monkeypatch):
        """-f outranks what the validating pass contributes."""
        monkeypatch.chdir(tmp_path)
        validation = Fixture.validation(tmp_path, filter_vars={"vhdl_std": "2008"})

        await validation.run()
        data = yaml.safe_load((tmp_path / "report.yaml").read_text())

        assert data["filter_vars"]["vhdl_std"] == "2008"

    async def test_warm_cache_still_reports_warnings(self, tmp_path, monkeypatch):
        """A second run hits the analysis cache and must report the same."""
        monkeypatch.chdir(tmp_path)
        repo_file = Fixture.write(tmp_path)

        first = await Fixture.validation(tmp_path, repo_file=repo_file).run()
        sidecars = list(tmp_path.glob("gbs-build/cache/**/*-diagnostics.json"))
        assert sidecars

        second = await Fixture.validation(tmp_path, repo_file=repo_file).run()

        assert [d.to_dict() for d in second.diagnostics] == \
               [d.to_dict() for d in first.diagnostics]
        assert second.status == "ok"

    async def test_analysis_error_still_reports(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        validation = Fixture.validation(tmp_path, top_source=Fixture.TOP_BROKEN)

        report = await validation.run()
        data = yaml.safe_load((tmp_path / "report.yaml").read_text())

        assert report.status == "error"
        assert data["status"] == "error"
        assert "error" in data
        # Resolution succeeded, so the tree is reported despite the failure.
        assert data["compile_order"] == ["libb.base", "liba.top"]

    async def test_unknown_partition(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        validation = Fixture.validation(tmp_path, partition_name="liba.nosuch")

        with pytest.raises(PartitionValidationError):
            await validation.run()

    async def test_vhdl_standard_override(self, tmp_path, monkeypatch):
        """The standard given with -c decides what GHDL accepts."""
        monkeypatch.chdir(tmp_path)
        repo_file = Fixture.write(
            tmp_path, top_source=Fixture.TOP_VHDL_2008, with_verilog=False
        )

        default = await Fixture.validation(tmp_path, repo_file=repo_file).run()
        assert default.status == "error"

        overridden = await Fixture.validation(
            tmp_path,
            repo_file=repo_file,
            backend_config={"vhdl_standard": "2008"},
        ).run()
        assert overridden.status == "ok"


@requires_ghdl
class TestCommandLine:
    """The command drives the same run from an empty directory."""

    @staticmethod
    def isolate(tmp_path, monkeypatch):
        """Set up a directory holding the repository and nothing else.

        No project file, no user config: the tree config is the only
        thing declaring the tool and the repository, which is the
        fallback path validation must work from.
        """
        repo_file = Fixture.write(tmp_path)
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gbs.yaml").write_text(yaml.safe_dump({
            "tools": [{"name": "ghdl", "variant": "test",
                       "config": {"executable": GHDL_PATH}}],
            "repositories": [{"path": str(repo_file)}],
        }))

    async def test_validate_reports_on_stdout(self, tmp_path, monkeypatch):
        self.isolate(tmp_path, monkeypatch)

        from asyncclick.testing import CliRunner
        from gbs.cli import cli

        result = await CliRunner().invoke(
            cli, ["partition", "validate", "-b", "ghdl", "liba.top"]
        )

        assert result.exit_code == 0, result.output
        data = yaml.safe_load(result.stdout)
        assert data["partition"] == "liba.top"
        assert data["status"] == "ok"

    async def test_unknown_partition_exits_one(self, tmp_path, monkeypatch):
        self.isolate(tmp_path, monkeypatch)

        from asyncclick.testing import CliRunner
        from gbs.cli import cli

        result = await CliRunner().invoke(
            cli, ["partition", "validate", "-b", "ghdl", "liba.nosuch"]
        )

        assert result.exit_code == 1


class TestOptionParsing:
    """Option shapes the command accepts."""

    def test_config_values_stay_strings(self):
        assert ValidateCommand.parse_config(("vhdl_standard=2008",)) == \
               {"vhdl_standard": "2008"}

    def test_backend_resolves_from_substring(self):
        names = ["gbs.builtin.ghdl", "gbs.builtin.nvc"]

        assert ValidateCommand.backend_resolve("ghdl", names) == "gbs.builtin.ghdl"

    def test_backend_rejects_unknown(self):
        import asyncclick as click

        with pytest.raises(click.ClickException):
            ValidateCommand.backend_resolve("nope", ["gbs.builtin.ghdl"])

    def test_missing_project_file_is_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        data, base = ValidateCommand.project_data_load(None)

        assert data == {}
        assert base == Path.cwd()

    def test_ambiguous_project_file_is_fatal(self, tmp_path, monkeypatch):
        import asyncclick as click

        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.gbs.yaml").write_text("name: a\n")
        (tmp_path / "b.gbs.yaml").write_text("name: b\n")

        with pytest.raises(click.ClickException):
            ValidateCommand.project_data_load(None)


class TestDiagnosticsSidecar:
    """Diagnostics survive a round trip through the sidecar file."""

    def test_roundtrip(self, tmp_path):
        from gbs.ui.messages import MessageSeverity, ToolMessage

        messages = [
            ToolMessage(severity=MessageSeverity.WARNING, message="beware",
                        file_path="top.vhd", line=7, column=5),
            ToolMessage(severity=MessageSeverity.INFO, message="tool chatter"),
        ]
        path = tmp_path / "diag.json"

        DiagnosticsSidecar.write(path, messages)
        records = DiagnosticsSidecar.read(path)

        # The location-less info line is chatter, not a diagnostic.
        assert len(records) == 1
        assert records[0] == Diagnostic(
            severity="warning", message="beware",
            file="top.vhd", line=7, column=5,
        )

    def test_empty_is_valid_content(self, tmp_path):
        path = tmp_path / "diag.json"

        DiagnosticsSidecar.write(path, [])

        assert path.exists()
        assert DiagnosticsSidecar.read(path) == []

    def test_missing_sidecar_reads_empty(self, tmp_path):
        assert DiagnosticsSidecar.read(tmp_path / "absent.json") == []
