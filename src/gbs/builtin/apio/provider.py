"""Apio package-tree toolchain provider."""

from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ...base import BaseToolchainProvider
from ...config.model import ToolConfig
from ...logging import get_logger
from ...utils import expand_path

logger = get_logger(__name__)


DEFAULT_APIO_ROOT = "~/.apio/packages"


@dataclass(frozen=True)
class ApioToolSpec:
    """One tool inside an apio package.

    Attributes:
        name: GBS tool name (matches what backends look up).
        default_variant: Variant to stamp when the toolchain entry
            does not declare one. `None` means "leave unset".
        relative_path: Executable location under the package root.
    """
    name: str
    default_variant: Optional[str]
    relative_path: str


PACKAGES: dict[str, list[ApioToolSpec]] = {
    "oss-cad-suite": [
        ApioToolSpec("yosys", None, "bin/yosys"),
        ApioToolSpec("nextpnr-ice40", None, "bin/nextpnr-ice40"),
        ApioToolSpec("nextpnr-ecp5", None, "bin/nextpnr-ecp5"),
        ApioToolSpec("nextpnr-machxo2", None, "bin/nextpnr-machxo2"),
        ApioToolSpec("nextpnr-nexus", None, "bin/nextpnr-nexus"),
        ApioToolSpec("nextpnr-himbaechel", None, "bin/nextpnr-himbaechel"),
        ApioToolSpec("nextpnr-generic", None, "bin/nextpnr-generic"),
        ApioToolSpec("icepack", None, "bin/icepack"),
        ApioToolSpec("ecppack", None, "bin/ecppack"),
        ApioToolSpec("gowin", None, "bin/gowin_pack"),
        ApioToolSpec("ghdl", "llvm", "bin/ghdl"),
        ApioToolSpec("nvc", None, "bin/nvc"),
        ApioToolSpec("verilator", None, "bin/verilator"),
    ],
    # openXC7 xilinx flow. Layout follows the toolchain-nix / apio
    # release tarballs; missing binaries are silently skipped so the
    # entry stays valid across releases.
    "tools-openxc7": [
        ApioToolSpec("nextpnr-xilinx", None, "bin/nextpnr-xilinx"),
        ApioToolSpec("xc7frames2bit", None, "bin/xc7frames2bit"),
        ApioToolSpec("fasm2frames", None, "bin/fasm2frames.py"),
        ApioToolSpec("bbasm", None, "bin/bbasm"),
    ],
}


class ApioToolchainProvider(BaseToolchainProvider):
    """Scans an apio package tree and yields ToolConfig entries.

    Options:
        root: Path to the apio packages directory. Defaults to
            ~/.apio/packages. Environment variables and `~` expand.
        packages: Optional list restricting which packages to scan.
            When omitted, every known package is considered.
        variant: Optional user-declared variant name stamped on every
            emitted tool. Use this to keep two apio installs
            selectable side-by-side (e.g. one entry with
            variant: apio-2024, another with variant: apio-2025).
            When set, the per-tool default_variant is ignored.
    """

    type = "apio"

    def enumerate_tools(self) -> list[ToolConfig]:
        root_option = self.options.get("root", DEFAULT_APIO_ROOT)
        root = expand_path(str(root_option))
        if not root.is_dir():
            logger.debug(
                f"Apio toolchain root {root} does not exist; provider is a no-op"
            )
            return []

        wanted = self.options.get("packages")
        if wanted is not None and not isinstance(wanted, list):
            logger.warning(
                f"Apio toolchain 'packages' must be a list, ignoring: {wanted!r}"
            )
            wanted = None
        wanted_set = set(wanted) if wanted is not None else None

        declared_variant = self.options.get("variant")
        if declared_variant is not None and not isinstance(declared_variant, str):
            logger.warning(
                f"Apio toolchain 'variant' must be a string, ignoring: {declared_variant!r}"
            )
            declared_variant = None

        tools: list[ToolConfig] = []
        for package_name, specs in PACKAGES.items():
            if wanted_set is not None and package_name not in wanted_set:
                continue
            package_root = root / package_name
            if not package_root.is_dir():
                logger.debug(f"Apio package {package_name!r} not installed at {package_root}")
                continue

            version = self._detect_package_version(package_root)

            for spec in specs:
                executable = package_root / spec.relative_path
                if not executable.exists():
                    logger.debug(
                        f"Apio package {package_name!r}: {spec.relative_path} not present, "
                        f"skipping tool {spec.name!r}"
                    )
                    continue
                variant = declared_variant if declared_variant is not None else spec.default_variant
                tools.append(ToolConfig(
                    name=spec.name,
                    variant=variant,
                    version=version,
                    config={"executable": str(executable)},
                    origin=self.origin,
                ))

        return tools

    @staticmethod
    def _detect_package_version(package_root: Path) -> Optional[str]:
        """Extract a version tag from a package's install metadata.

        Currently reads BUILD-INFO.json (present in oss-cad-suite and
        FPGAwars-built tarballs) and returns its `release-tag` field.
        Silently returns None if the file is missing or malformed - the
        provider must still emit tools without a version tag.
        """
        info_path = package_root / "BUILD-INFO.json"
        if not info_path.is_file():
            return None
        try:
            with info_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.debug(f"Failed to parse {info_path}: {e}")
            return None
        tag = data.get("release-tag")
        if isinstance(tag, str) and tag:
            return tag
        return None
