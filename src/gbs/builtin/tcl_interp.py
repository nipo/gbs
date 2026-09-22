"""Tcl interpreter discovery for backends that must evaluate Tcl.

Vivado's XDC is a Tcl program, so translating one means running a real
interpreter rather than pattern-matching the file. This module finds an
interpreter to host that evaluation.

A standalone ``tclsh`` is the host: macOS ships 8.5 in ``/usr/bin`` and
every distribution packages one, so it is available far more reliably
than the alternatives. Its name carries the version on most systems
(``tclsh8.6``, ``tclsh9.0``), and which of those exist varies per
machine, so candidates are discovered by scanning ``PATH`` instead of
guessing a fixed list of names.

Yosys' embedded ``tcl`` command is kept as a last resort, for a machine
carrying a Tcl-enabled yosys but no interpreter of its own. It is a
build-time option (``ENABLE_TCL``) that several distributions leave off
-- the oss-cad-suite macOS build among them -- which is why it cannot be
the primary host.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ..utils import expand_path

if TYPE_CHECKING:
    from ..config.model import GBSConfig

__all__ = ["TclInterpreter", "MINIMUM_VERSION"]


#: Oldest Tcl the generated preambles run on. They use ``dict``, which
#: arrived in 8.5; macOS ships exactly 8.5, so this is the floor the
#: platform sets rather than an arbitrary choice.
MINIMUM_VERSION = (8, 5)

#: ``tclsh``, ``tclsh8.6``, ``tclsh9.0``, plus the Windows spellings.
_TCLSH_NAME = re.compile(r"^tclsh(?:(\d+)\.(\d+))?(?:\.exe)?$", re.IGNORECASE)

_VERSION_SCRIPT = "puts $tcl_version\n"


@dataclass(frozen=True)
class TclInterpreter:
    """A resolved Tcl interpreter and how to hand it a script.

    Attributes:
        executable: Absolute path to the binary.
        host: ``tclsh`` for a standalone interpreter, ``yosys`` for the
            one embedded in yosys.
        version: Tcl version, when the host reports one.
    """

    executable: str
    host: str
    version: tuple[int, int] | None = None

    def argv(self, script: Path) -> list[str]:
        """Command line running ``script`` in this interpreter."""
        if self.host == "yosys":
            return [self.executable, "-q", "-p", f"tcl {script}"]
        return [self.executable, str(script)]

    def __str__(self) -> str:
        if self.version:
            return f"{self.executable} (Tcl {self.version[0]}.{self.version[1]})"
        return f"{self.executable} (embedded Tcl)"

    @classmethod
    def resolve(
        cls,
        gbs_config: "GBSConfig | None",
        tcl_identifier: str = "tclsh",
        yosys_identifier: str | None = None,
    ) -> "TclInterpreter | None":
        """Find an interpreter, or None when the machine has none.

        Search order: the tool named by ``tcl_identifier`` in the user's
        configuration, then ``tclsh`` binaries on PATH, then yosys'
        embedded interpreter when ``yosys_identifier`` names a
        configured yosys that was built with it.

        An explicitly configured interpreter is used even when its
        version cannot be read, so a wrapper script that does not answer
        ``puts $tcl_version`` still works; discovered candidates must
        report a version at or above :data:`MINIMUM_VERSION`, since
        there the point is to pick the right one among several.
        """
        configured = cls.__configured_executable(gbs_config, tcl_identifier)
        if configured:
            return cls(configured, "tclsh", cls.tcl_version(configured))

        for executable in cls.candidates():
            version = cls.tcl_version(executable)
            if version is not None and version >= MINIMUM_VERSION:
                return cls(executable, "tclsh", version)

        if yosys_identifier:
            yosys = cls.__configured_executable(gbs_config, yosys_identifier)
            if yosys and cls.yosys_has_tcl(yosys):
                return cls(yosys, "yosys")

        return None

    @classmethod
    def rejection_reason(
        cls,
        gbs_config: "GBSConfig | None",
        tcl_identifier: str = "tclsh",
        yosys_identifier: str | None = None,
    ) -> str | None:
        """Probe-style reason string, or None when an interpreter exists."""
        if cls.resolve(gbs_config, tcl_identifier, yosys_identifier) is not None:
            return None
        return (
            f"no Tcl interpreter available: no {tcl_identifier!r} tool is "
            f"configured, no tclsh {MINIMUM_VERSION[0]}.{MINIMUM_VERSION[1]} "
            f"or newer is on PATH, and no configured yosys provides the 'tcl' "
            f"command. Install Tcl, or declare a tclsh tool in gbs config."
        )

    @staticmethod
    def candidates() -> list[str]:
        """``tclsh`` binaries on PATH, best first.

        The unversioned name comes first, in PATH order: it is the
        interpreter the machine already elected as its default, and
        respecting that beats preferring the highest version we can
        find. Versioned names follow, newest first, so a system that
        ships only ``tclsh8.6`` is still served.
        """
        plain: list[str] = []
        versioned: list[tuple[tuple[int, int], str]] = []
        seen: set[Path] = set()

        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            directory = Path(entry)
            try:
                names = sorted(os.listdir(directory))
            except OSError:
                continue
            for name in names:
                match = _TCLSH_NAME.match(name)
                if match is None:
                    continue
                path = directory / name
                if not os.access(path, os.X_OK) or path.is_dir():
                    continue
                # Homebrew's tclsh and tclsh9.0 are the same binary
                # behind two symlinks; keep whichever name is seen
                # first and drop the duplicate.
                try:
                    identity = path.resolve()
                except OSError:
                    identity = path
                if identity in seen:
                    continue
                seen.add(identity)
                if match.group(1) is None:
                    plain.append(str(path))
                else:
                    version = (int(match.group(1)), int(match.group(2)))
                    versioned.append((version, str(path)))

        versioned.sort(key=lambda item: item[0], reverse=True)
        return plain + [path for _, path in versioned]

    @staticmethod
    @lru_cache(maxsize=None)
    def tcl_version(executable: str) -> tuple[int, int] | None:
        """Tcl version the binary reports, or None when it cannot run.

        Cached: planning probes run once per output group, and a build
        of many groups would otherwise re-launch the same interpreter
        for every one of them.
        """
        try:
            result = subprocess.run(
                [executable],
                input=_VERSION_SCRIPT,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        match = re.match(r"(\d+)\.(\d+)", result.stdout.strip())
        if match is None:
            return None
        return (int(match.group(1)), int(match.group(2)))

    @staticmethod
    @lru_cache(maxsize=None)
    def yosys_has_tcl(executable: str) -> bool:
        """Whether this yosys was built with the ``tcl`` command.

        Sourcing an empty script is the only reliable test: ``help tcl``
        exits 0 and prints nothing whether or not the command exists.
        """
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "probe.tcl"
            empty.write_text("")
            try:
                result = subprocess.run(
                    [executable, "-q", "-p", f"tcl {empty}"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except (OSError, subprocess.SubprocessError):
                return False
        return result.returncode == 0

    @staticmethod
    def __configured_executable(
        gbs_config: "GBSConfig | None",
        identifier: str,
    ) -> str | None:
        """Executable of a configured tool, when it exists on disk."""
        if gbs_config is None:
            return None
        tool = gbs_config.get_tool(identifier)
        if tool is None:
            return None
        for key in ("executable", "path"):
            raw = tool.config.get(key)
            if not raw:
                continue
            resolved = expand_path(raw)
            return str(resolved) if resolved.exists() else None
        return None
