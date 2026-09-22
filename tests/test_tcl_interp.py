"""Tests for Tcl interpreter discovery."""

import os
import stat
from pathlib import Path

import pytest

from gbs.builtin.tcl_interp import MINIMUM_VERSION, TclInterpreter


def _fake_tclsh(directory: Path, name: str, version: str | None) -> Path:
    """Create an executable answering `puts $tcl_version` with version.

    A None version makes a binary that exits nonzero, standing in for
    one that is installed but unusable.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if version is None:
        path.write_text("#!/bin/sh\nexit 1\n")
    else:
        path.write_text(f"#!/bin/sh\ncat >/dev/null\necho {version}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def only_path(monkeypatch):
    """Restrict PATH to the directories the test creates."""
    def use(*directories: Path):
        monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in directories))
        TclInterpreter.tcl_version.cache_clear()
    yield use
    TclInterpreter.tcl_version.cache_clear()


def test_unversioned_name_wins_over_higher_version(tmp_path, only_path):
    """The machine's own default is respected, not the highest version."""
    _fake_tclsh(tmp_path / "a", "tclsh", "8.6")
    _fake_tclsh(tmp_path / "b", "tclsh9.0", "9.0")
    only_path(tmp_path / "a", tmp_path / "b")

    interp = TclInterpreter.resolve(None)
    assert interp.executable == str(tmp_path / "a" / "tclsh")
    assert interp.version == (8, 6)


def test_versioned_candidates_ordered_newest_first(tmp_path, only_path):
    _fake_tclsh(tmp_path / "a", "tclsh8.6", "8.6")
    _fake_tclsh(tmp_path / "a", "tclsh9.1", "9.1")
    _fake_tclsh(tmp_path / "a", "tclsh8.5", "8.5")
    only_path(tmp_path / "a")

    assert [Path(c).name for c in TclInterpreter.candidates()] == [
        "tclsh9.1", "tclsh8.6", "tclsh8.5",
    ]


def test_unversioned_precedes_versioned_in_candidates(tmp_path, only_path):
    _fake_tclsh(tmp_path / "a", "tclsh9.0", "9.0")
    _fake_tclsh(tmp_path / "b", "tclsh", "8.5")
    only_path(tmp_path / "a", tmp_path / "b")

    assert [Path(c).name for c in TclInterpreter.candidates()] == [
        "tclsh", "tclsh9.0",
    ]


def test_too_old_is_skipped(tmp_path, only_path):
    """Tcl 8.4 lacks `dict`, which the generated preamble needs."""
    _fake_tclsh(tmp_path / "a", "tclsh", "8.4")
    _fake_tclsh(tmp_path / "b", "tclsh8.6", "8.6")
    only_path(tmp_path / "a", tmp_path / "b")

    interp = TclInterpreter.resolve(None)
    assert interp.version == (8, 6)
    assert interp.version >= MINIMUM_VERSION


def test_unusable_binary_is_skipped(tmp_path, only_path):
    _fake_tclsh(tmp_path / "a", "tclsh", None)
    _fake_tclsh(tmp_path / "b", "tclsh8.6", "8.6")
    only_path(tmp_path / "a", tmp_path / "b")

    assert TclInterpreter.resolve(None).version == (8, 6)


def test_symlinked_duplicate_listed_once(tmp_path, only_path):
    """Homebrew ships tclsh and tclsh9.0 as one binary behind two names."""
    real = _fake_tclsh(tmp_path / "a", "tclsh9.0", "9.0")
    (tmp_path / "a" / "tclsh").symlink_to(real)
    only_path(tmp_path / "a")

    assert len(TclInterpreter.candidates()) == 1


def test_no_interpreter_gives_reason(tmp_path, only_path):
    only_path(tmp_path / "empty")

    assert TclInterpreter.resolve(None) is None
    reason = TclInterpreter.rejection_reason(None)
    assert "no Tcl interpreter available" in reason


def test_argv_per_host(tmp_path):
    script = tmp_path / "s.tcl"
    assert TclInterpreter("/bin/tclsh", "tclsh").argv(script) == [
        "/bin/tclsh", str(script),
    ]
    assert TclInterpreter("/bin/yosys", "yosys").argv(script) == [
        "/bin/yosys", "-q", "-p", f"tcl {script}",
    ]


# --- Configured tools --------------------------------------------------------

def _config(*tools):
    from gbs.config.model import GBSConfig, ToolConfig
    config = GBSConfig()
    for name, executable in tools:
        config.tools.append(ToolConfig(name, None, None, {"executable": str(executable)}))
    return config


def test_configured_tclsh_beats_path(tmp_path, only_path):
    _fake_tclsh(tmp_path / "a", "tclsh", "9.0")
    chosen = _fake_tclsh(tmp_path / "pinned", "my-tclsh", "8.6")
    only_path(tmp_path / "a")

    interp = TclInterpreter.resolve(_config(("tclsh", chosen)))
    assert interp.executable == str(chosen)


def test_configured_tclsh_missing_on_disk_falls_through(tmp_path, only_path):
    _fake_tclsh(tmp_path / "a", "tclsh", "9.0")
    only_path(tmp_path / "a")

    interp = TclInterpreter.resolve(_config(("tclsh", tmp_path / "gone")))
    assert interp.executable == str(tmp_path / "a" / "tclsh")


def test_yosys_fallback_only_when_no_tclsh(tmp_path, only_path, monkeypatch):
    yosys = tmp_path / "bin" / "yosys"
    yosys.parent.mkdir(parents=True)
    yosys.write_text("#!/bin/sh\nexit 0\n")
    yosys.chmod(0o755)
    monkeypatch.setattr(TclInterpreter, "yosys_has_tcl", staticmethod(lambda e: True))
    config = _config(("yosys", yosys))

    only_path(tmp_path / "empty")
    interp = TclInterpreter.resolve(config, yosys_identifier="yosys")
    assert interp.host == "yosys"

    _fake_tclsh(tmp_path / "a", "tclsh", "8.6")
    only_path(tmp_path / "a")
    assert TclInterpreter.resolve(config, yosys_identifier="yosys").host == "tclsh"


def test_yosys_without_tcl_is_not_an_interpreter(tmp_path, only_path, monkeypatch):
    yosys = tmp_path / "bin" / "yosys"
    yosys.parent.mkdir(parents=True)
    yosys.write_text("#!/bin/sh\nexit 0\n")
    yosys.chmod(0o755)
    monkeypatch.setattr(TclInterpreter, "yosys_has_tcl", staticmethod(lambda e: False))
    only_path(tmp_path / "empty")

    assert TclInterpreter.resolve(
        _config(("yosys", yosys)), yosys_identifier="yosys") is None
